from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Protocol
from urllib.parse import urlparse

import httpx

from .config import AppConfig
from .completion_prompt import SYSTEM_PROMPT, EXAMPLES
from .diagnostics import log_event
from .sessions import Message

Emit = Callable[[str], None]


def _run_cancellable(factory, cancelled, timeout: float) -> str:
    """Cancel the socket read itself, including before headers/first token arrive."""
    async def run():
        if cancelled():
            return ""
        async def watch():
            while not cancelled():
                await asyncio.sleep(.02)
        request = asyncio.create_task(factory())
        cancellation = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((request, cancellation), timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancelled():
                return ""
            if request in done:
                return await request
            raise TimeoutError("Completion request exceeded its time limit")
        finally:
            request.cancel()
            cancellation.cancel()
            await asyncio.gather(request, cancellation, return_exceptions=True)
    return asyncio.run(run())


@dataclass(frozen=True)
class SuggestionRequest:
    messages: list[Message]
    draft: str


class SuggestionBackend(Protocol):
    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str: ...


MAX_SUGGESTION_CHARS = 360
COMPLETION_TOKENS = 512
CONTEXT_TOKENS = 4096

CONTINUATION_SCHEMA = {
    "type": "object",
    "properties": {"continuation": {"type": "string"}},
    "required": ["continuation"],
    "additionalProperties": False,
}


def draft_anchor(draft: str) -> str:
    # Keep control characters out of Ollama's regex-constrained JSON string;
    # the full multiline draft remains available in the quoted input.
    line = re.split(r"[\r\n\t]", draft)[-1]
    # Keep the unfinished Chinese sentence as the anchor. Copying several
    # repeated earlier sentences can make a small model stop at the anchor.
    fragment = re.split(r"[。！？]", line)[-1]
    return (fragment if fragment.strip() else line)[-48:]


def continuation_schema(draft: str) -> dict:
    # Ollama's constrained decoder copies the anchor exactly. This prevents a
    # long/repetitive draft from being paraphrased before we extract its suffix.
    literal = re.escape(draft_anchor(draft))
    return {**CONTINUATION_SCHEMA, "properties": {
        "continuation": {"type": "string", "pattern": "^" + literal + ".*$"}}}

def completion_background(messages: list[Message]) -> list[Message]:
    """Exclude quoted generation policies, while retaining adjacent task context.

    A small local model can paraphrase these policies even when clearly quoted.
    Match output-control language together with completion terminology, not
    vague drafts, domain nouns, or ordinary numbered requirements.
    """
    result = []
    for message in messages:
        paragraphs = []
        for paragraph in message.text.splitlines():
            completion = re.search(r"补全|续写|扩写|补充|生成规则|continuation|complet(?:ion|e)", paragraph, re.I)
            controls = re.search(r"(?:控制|限制|总|目标).{0,12}(?:字数|字符|中文字)|返回.{0,8}JSON|只输出|生成规则|word limit|return.{0,8}json", paragraph, re.I)
            if not (completion and controls):
                paragraphs.append(paragraph)
        text = "\n".join(paragraphs).strip()
        if text:
            result.append(Message(message.role, text))
    return result


def _completion_input(messages: list[Message], draft: str) -> str:
    # History is quoted task data, never live assistant/user turns. Escape Qwen
    # control-token openers even when the server applies its native template.
    data = {"background": [{"speaker": m.role, "text": m.text}
                           for m in completion_background(messages)],
            "draft": draft[-1000:], "anchor": draft_anchor(draft)}
    return json.dumps(data, ensure_ascii=False).replace("<|", "< |")


def build_messages(request: SuggestionRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for background, draft, suffix in EXAMPLES:
        messages.extend([
            {"role": "user", "content": _completion_input(background, draft)},
            {"role": "assistant", "content": json.dumps({"continuation": draft_anchor(draft) + suffix}, ensure_ascii=False)},
        ])
    messages.append({"role": "user", "content": _completion_input(request.messages, request.draft)})
    return messages


def can_continue(draft: str) -> bool:
    text = draft.strip()
    return bool(text) and not text.endswith(("?", "？"))


def normalize_suggestion(raw: str, draft: str, limit: int = MAX_SUGGESTION_CHARS) -> str:
    if draft.rstrip().endswith(("?", "？")):
        return ""  # A completed question must never become its own answer.
    if "<think>" in raw and "</think>" not in raw:
        return ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = raw.rstrip() if raw.strip() else ""
    if draft and raw.startswith(draft):
        raw = raw[len(draft) :]
    elif draft and draft.startswith(raw):
        return ""  # A partial echo of the draft is not a continuation.
    if len(raw) <= limit:
        return raw
    # Keep complete requirements instead of cutting a word or sentence in half.
    boundaries = [m for m in re.finditer(r"[。！？；;]|[.!?](?=\s|$)", raw) if m.end() <= limit]
    return raw[:boundaries[-1].end()] if boundaries else ""


def decode_suggestion(raw: str, draft: str) -> str:
    """Parse a completed suffix response; never show JSON or partial protocol text."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("continuation"), str):
            raise ValueError("missing continuation")
    except (ValueError, TypeError) as exc:
        log_event("completion_output", reason="invalid_format", raw_len=len(raw), suggestion_len=0)
        raise ValueError("Completion model must return a JSON object with a string continuation") from exc
    anchor = draft_anchor(draft)
    if not value["continuation"].startswith(anchor):
        log_event("completion_output", reason="changed_anchor", raw_len=len(raw), suggestion_len=0)
        raise ValueError("Completion model changed the existing draft")
    suffix = "" if draft.rstrip().endswith(("?", "？")) else normalize_suggestion(value["continuation"][len(anchor):], "")
    reason = "suffix" if suffix else "model_empty"
    log_event("completion_output", reason=reason, raw_len=len(raw), suggestion_len=len(suffix))
    return suffix


def repeats_input(suffix: str, request: SuggestionRequest) -> bool:
    """Detect substantial copied prose, not a confidence score or shared nouns."""
    compact = lambda text: re.sub(r"[\W_]+", "", text.casefold())
    candidate = compact(suffix)
    if len(candidate) < 10:
        return False
    for source in (SYSTEM_PROMPT, request.draft, *(m.text for m in request.messages)):
        original = compact(source)
        matches = SequenceMatcher(None, candidate, original, autojunk=False).get_matching_blocks()
        longest = max(m.size for m in matches)
        copied = sum(m.size for m in matches)
        if (len(re.findall(r"[\u3400-\u9fff]", candidate)) >= 10
                and longest >= 10 and copied >= len(candidate) * .7):
            return True
        if longest >= 32 or (longest >= 16 and copied >= len(candidate) * .65):
            return True
    return False


async def complete_request(read, request: SuggestionRequest) -> str:
    """One adaptive continuation, with at most one repair under the same deadline."""
    schema = continuation_schema(request.draft)
    messages = build_messages(request)
    for attempt in range(2):
        raw = await read(messages, schema, COMPLETION_TOKENS)
        try:
            suffix = decode_suggestion(raw, request.draft)
            if suffix and not repeats_input(suffix, request):
                return suffix
        except ValueError:
            pass
        if attempt == 0:
            log_event("completion_retry", reason="invalid_continuation")
            # Do not feed the bad response back to the model. Remove copied
            # background while preserving the user's exact current draft.
            messages = build_messages(SuggestionRequest([], request.draft))
            messages[0]["content"] += "\n重新续写：保留 anchor，接上新的文字；不要复述背景、草稿或生成规则。"
    return ""


def _local_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Ollama must use a local HTTP address")
    return base_url.rstrip("/")


class OllamaBackend:
    def __init__(self, base_url: str, model: str, transport: httpx.BaseTransport | None = None,
                 *, keep_alive_seconds: int = 60, request_timeout: float = 8.0) -> None:
        self.base_url = _local_url(base_url)
        self.model = model
        self.transport = transport
        self.keep_alive_seconds = max(0, min(int(keep_alive_seconds), 3600))
        self.request_timeout = request_timeout
        self._network_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._active: set[threading.Event] = set()
        self._activity_version = 0
        # Never send localhost conversation data through HTTP(S)_PROXY.
        self.client = httpx.Client(
            transport=transport,
            trust_env=False,
            timeout=httpx.Timeout(35.0, connect=2.0),
        )

    def list_models(self) -> list[tuple[str, int]]:
        response = self.client.get(f"{self.base_url}/api/tags", timeout=2)
        response.raise_for_status()
        def model_size(value: object) -> int:
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

        return sorted(
            ((item["name"], model_size(item.get("size")))
             for item in response.json().get("models", [])
             if isinstance(item, dict) and isinstance(item.get("name"), str)),
            key=lambda item: item[1],
        )

    def available(self) -> tuple[bool, bool]:
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        return True, any(name == wanted for name, _ in self.list_models())

    def warm(self) -> None:
        payload = {"model": self.model, "keep_alive": self.keep_alive_seconds, "stream": False,
                   "think": False, "options": {"num_predict": 1, "num_ctx": CONTEXT_TOKENS},
                   "messages": build_messages(SuggestionRequest([], "你好")), "format": continuation_schema("你好")}
        async def warm():
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, timeout=90) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
            return ""
        self._operation(warm, threading.Event(), 90)

    def _operation(self, factory, cancel, timeout):
        local_cancel = threading.Event()
        with self._lifecycle_lock:
            self._activity_version += 1
            self._active.add(local_cancel)
        cancelled = lambda: cancel.is_set() or local_cancel.is_set()
        try:
            while not self._network_lock.acquire(timeout=.02):
                if cancelled():
                    return ""
            try:
                return _run_cancellable(factory, cancelled, timeout)
            finally:
                self._network_lock.release()
        finally:
            with self._lifecycle_lock:
                self._active.discard(local_cancel)

    def release_async(self) -> threading.Thread:
        # Register the release now, before the background thread starts. A later
        # resume/request supersedes it; an earlier warm cannot reload after it.
        with self._lifecycle_lock:
            version = self._activity_version
            for event in self._active:
                event.set()
        def release():
            with self._network_lock:
                with self._lifecycle_lock:
                    if version != self._activity_version:
                        return
                try:
                    response = self.client.post(f"{self.base_url}/api/generate",
                                                json={"model": self.model, "keep_alive": 0}, timeout=2)
                    response.raise_for_status()
                    log_event("model_released", shown=True)
                except Exception as exc:
                    log_event("model_release_failed", error_type=type(exc).__name__.lower())
        thread = threading.Thread(target=release, daemon=True)
        thread.start()
        return thread

    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str:
        if not can_continue(request.draft):
            return ""
        payload = {"model": self.model, "stream": True, "keep_alive": self.keep_alive_seconds,
                   "think": False, "messages": build_messages(request), "format": continuation_schema(request.draft),
                   "options": {"temperature": 0, "num_predict": COMPLETION_TOKENS, "num_ctx": CONTEXT_TOKENS,
                               "repeat_penalty": 1.0}}
        async def stream(messages, schema, budget):
            raw = ""
            payload["messages"] = messages
            payload["format"] = schema
            payload["options"]["num_predict"] = budget
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         timeout=self.request_timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        raw += chunk.get("message", {}).get("content", "")
                        if chunk.get("done"):
                            break
            return raw
        final = self._operation(lambda: complete_request(stream, request), cancel, self.request_timeout)
        if cancel.is_set() or not final:
            return ""
        if final:
            emit(final)
        return final

    def close(self) -> None:
        with self._lifecycle_lock:
            for event in self._active:
                event.set()
        self.client.close()


def make_backend(config: AppConfig) -> OllamaBackend:
    return OllamaBackend(config.ollama_url, config.ollama_model,
                         keep_alive_seconds=config.model_idle_seconds,
                         request_timeout=config.request_timeout_seconds)
