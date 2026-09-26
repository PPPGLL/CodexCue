from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Callable, Protocol
from urllib.parse import urlparse

import httpx

from .config import AppConfig
from .completion_prompt import SYSTEM_PROMPT, EXAMPLES
from .completion_models import (NATIVE_INSTRUCTION, NATIVE_STOPS, NATIVE_TOKENS,
                                escape_native_input, native_completion_family, native_segment)
from .diagnostics import log_event
from .sessions import Message
from .token_budget import TokenCounter

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
CONTEXT_TOKENS = 16384
REPAIR_INSTRUCTION = ("\nContinue at the cursor with new words, not punctuation alone. "
                      "Start the JSON continuation with the exact anchor, not the entire draft. "
                      "Finish the unfinished phrase first; if already complete, predict the user's "
                      "next short sentence. Do not add a plan or checklist unless the user is "
                      "already writing one. Do not repeat background or writing rules.")

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
    # Keep a short, verbatim cursor anchor. Copying a whole sentence increases
    # output work and can make the model finish the copied thought too early.
    fragment = re.split(r"[。！？]", line)[-1]
    return (fragment if fragment.strip() else line)[-12:]


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
            result.append(replace(message, text=text))
    return result


def _completion_input(messages: list[Message], draft: str) -> str:
    # History is quoted task data, never live assistant/user turns. Escape Qwen
    # control-token openers even when the server applies its native template.
    data = {"background": [{"speaker": m.kind if m.kind == "task_summary" else m.role, "text": m.text}
                           for m in completion_background(messages)],
            "draft": draft, "anchor": draft_anchor(draft)}
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


def build_native_prompt(request: SuggestionRequest, *, repair: bool = False) -> str:
    def background(messages):
        return escape_native_input(json.dumps(
            [{"speaker": m.kind if m.kind == "task_summary" else m.role, "text": m.text}
             for m in completion_background(messages)],
            ensure_ascii=False))

    prompt = NATIVE_INSTRUCTION
    if repair:
        prompt += "Write new words at the cursor, not punctuation alone or copied background.\n\n"
    # Complete user messages teach the base LM which voice it is continuing.
    # Reuse the chat examples so model comparisons do not change task examples.
    for context, draft, suffix in EXAMPLES:
        prompt += f"Background: {background(context)}\nUser: {escape_native_input(draft + suffix)}\n\n"
    return prompt + f"Background: {background(request.messages)}\nUser: {escape_native_input(request.draft)}"


def fit_request(request: SuggestionRequest, counter: TokenCounter, *, native: bool = False) -> SuggestionRequest:
    """Spend the model's token budget on recent dialogue and older task context.

    Keep original message order for prefix-cache reuse. Selection runs on the
    inference worker, not the desktop input thread; no model call is involved.
    """
    output = NATIVE_TOKENS if native else COMPLETION_TOKENS
    ceiling = CONTEXT_TOKENS - output - max(128, counter.count(REPAIR_INSTRUCTION) + 32)

    def count(candidate):
        if native:
            return counter.count(build_native_prompt(candidate)) + 128
        return counter.chat_tokens(build_messages(candidate))

    messages = completion_background(request.messages)
    empty = count(SuggestionRequest([], ""))

    def history_ceiling(draft):
        # Stable 256-token buckets leave typing room without moving the start
        # of history on every keystroke (which invalidates Ollama's KV cache).
        # Count the serialized draft and anchor, including JSON escaping.
        cost = max(0, count(SuggestionRequest([], draft)) - empty)
        return ceiling - ((cost + 32 + 255) // 256) * 256

    draft = request.draft
    limit = history_ceiling(draft)
    if count(SuggestionRequest(messages, "")) <= limit:
        return SuggestionRequest(messages, draft)
    # An exceptionally long pasted draft must leave room for its background.
    draft_budget = CONTEXT_TOKENS // 4 if messages else ceiling // 2
    draft = counter.clip(request.draft, draft_budget, tail=True)
    selected: dict[int, Message] = {}

    def candidate():
        # Select against an empty draft and its reserved bucket. Keeping this
        # exact prefix stable also reuses cached token counts during edits.
        return SuggestionRequest([selected[i] for i in sorted(selected)], "")

    limit = history_ceiling(draft)
    while empty > limit:
        # JSON escaping can make control-character-heavy pastes much larger
        # than their plain-text token count. Budget the serialized prompt too.
        draft = counter.clip(draft, max(1, counter.count(draft) // 2), tail=True)
        limit = history_ceiling(draft)

    def add(index, limit):
        message = messages[index]
        selected[index] = message
        if count(candidate()) <= limit:
            return
        # Trim only a message that does not fit, on Unicode character boundaries.
        # Older summaries retain their opening task/constraints; recent dialogue
        # retains its newest text, including corrections at the end of a turn.
        left, right = 0, len(message.text)
        while left < right:
            middle = (left + right + 1) // 2
            text = message.text[:middle] if message.kind == "task_summary" else message.text[-middle:]
            selected[index] = replace(message, text=text)
            if count(candidate()) <= limit:
                left = middle
            else:
                right = middle - 1
        if left:
            text = message.text[:left] if message.kind == "task_summary" else message.text[-left:]
            selected[index] = replace(message, text=text)
        else:
            del selected[index]

    summary = next((i for i in reversed(range(len(messages))) if messages[i].kind == "task_summary"), None)
    dialogue = [i for i, m in enumerate(messages) if m.kind == "dialogue"]
    latest_user = next((i for i in reversed(dialogue) if messages[i].role == "user"), None)
    # Reserve some room for the older task state only when it exists; unused
    # summary space is returned to the rest of the conversation below.
    base = count(candidate())
    summary_room = min(counter.count(messages[summary].text) + 24, (limit - base) // 3) if summary is not None else 0
    recent_limit = limit - summary_room
    if latest_user is not None:
        add(latest_user, recent_limit)
    if dialogue and dialogue[-1] != latest_user:
        add(dialogue[-1], recent_limit)
    if summary is not None:
        add(summary, limit)
    for index in reversed(dialogue):
        if index not in selected:
            add(index, limit)
            if count(candidate()) >= limit - 32:
                break
    return replace(candidate(), draft=draft)


def decode_native_suggestion(raw: str) -> str:
    # Servers should obey stop sequences, but also strip protocol boundaries
    # before displaying output from custom transports or interrupted streams.
    for stop in NATIVE_STOPS:
        raw = raw.split(stop, 1)[0]
    return normalize_suggestion(native_segment(raw, final=True), "")


async def complete_native_request(read, request: SuggestionRequest) -> str:
    current = request
    for attempt in range(2):
        raw = await read(build_native_prompt(current, repair=bool(attempt)), NATIVE_TOKENS)
        suffix = decode_native_suggestion(raw)
        copied = bool(suffix) and repeats_input(suffix, request)
        if suffix and not copied:
            return suffix
        if attempt == 0:
            log_event("completion_retry", reason="invalid_continuation")
            if copied:
                current = SuggestionRequest([], request.draft)
    return ""


def can_continue(draft: str) -> bool:
    return bool(draft.strip())


def normalize_suggestion(raw: str, draft: str, limit: int = MAX_SUGGESTION_CHARS) -> str:
    if "<think>" in raw and "</think>" not in raw:
        return ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = raw.rstrip() if raw.strip() else ""
    if draft and raw.startswith(draft):
        raw = raw[len(draft) :]
    elif draft and draft.startswith(raw):
        return ""  # A partial echo of the draft is not a continuation.
    if not any(char.isalnum() for char in raw):
        return ""  # A lone separator is not useful writing; request a real continuation.
    if len(raw) <= limit:
        return raw
    # Keep complete sentences instead of cutting a word or sentence in half.
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
    continuation = value["continuation"]
    if draft and continuation.startswith(draft):
        # Some local models copy the exact full draft despite the short-anchor
        # instruction. Extract only new text; never accept a rewritten prefix.
        suffix = continuation[len(draft):]
    elif continuation.startswith(anchor):
        suffix = continuation[len(anchor):]
    else:
        log_event("completion_output", reason="changed_anchor", raw_len=len(raw), suggestion_len=0)
        raise ValueError("Completion model changed the existing draft")
    suffix = normalize_suggestion(suffix, "")
    reason = "suffix" if suffix else "model_empty"
    log_event("completion_output", reason=reason, raw_len=len(raw), suggestion_len=len(suffix))
    return suffix


def repeats_input(suffix: str, request: SuggestionRequest) -> bool:
    """Detect substantial copied prose, not a confidence score or shared nouns."""
    compact = lambda text: re.sub(r"[\W_]+", "", text.casefold())
    candidate = compact(suffix)
    if len(candidate) < 10:
        return False
    for index, source in enumerate((SYSTEM_PROMPT, NATIVE_INSTRUCTION, request.draft,
                                    *(m.text for m in request.messages))):
        original = compact(source)
        if index >= 3 and candidate != original:
            # A short factual fragment may need to repeat known context to
            # complete the draft (e.g. a changed color plus its constraint).
            # Reject wholesale echoes/long copied passages, not that reuse.
            threshold = 24 if re.search(r"[\u3400-\u9fff]", candidate) else 64
            if len(candidate) < threshold:
                continue
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
    """One cursor continuation, with at most one repair under the same deadline."""
    schema = continuation_schema(request.draft)
    messages = build_messages(request)
    for attempt in range(2):
        raw = await read(messages, schema, COMPLETION_TOKENS)
        copied = False
        try:
            suffix = decode_suggestion(raw, request.draft)
            copied = bool(suffix) and repeats_input(suffix, request)
            if suffix and not copied:
                return suffix
        except ValueError:
            pass
        if attempt == 0:
            log_event("completion_retry", reason="invalid_continuation")
            # Keep facts for format/empty-output repairs. Remove contaminated
            # background only when the model actually copied it.
            messages = build_messages(SuggestionRequest([] if copied else request.messages, request.draft))
            messages[0]["content"] += REPAIR_INSTRUCTION
    return ""


def _local_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Ollama must use a local HTTP address")
    return base_url.rstrip("/")


class OllamaBackend:
    def __init__(self, base_url: str, model: str, transport: httpx.BaseTransport | None = None,
                 *, request_timeout: float = 8.0) -> None:
        self.base_url = _local_url(base_url)
        self.model = model
        self.completion_family = native_completion_family(model)
        self.transport = transport
        self.request_timeout = request_timeout
        self._network_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._active: set[threading.Event] = set()
        self._closed = False
        self.token_counter = TokenCounter()
        self._tokenizer_loaded = False
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
        payload = {"model": self.model, "keep_alive": -1, "stream": False,
                   "think": False, "options": {"num_predict": 1, "num_ctx": CONTEXT_TOKENS},
                   "messages": build_messages(SuggestionRequest([], "你好")), "format": continuation_schema("你好")}
        endpoint = "/api/chat"
        if self.completion_family:
            endpoint = "/api/generate"
            payload = self._native_payload(SuggestionRequest([], "你好"), stream=False)
            payload["options"]["num_predict"] = 1
        async def warm():
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, timeout=90) as client:
                if not self._tokenizer_loaded:
                    try:
                        details = await client.post(f"{self.base_url}/api/show",
                                                    json={"model": self.model, "verbose": True}, timeout=5)
                        details.raise_for_status()
                        self.token_counter = TokenCounter.from_model_info(details.json()["model_info"])
                        self._tokenizer_loaded = True
                    except Exception as exc:
                        log_event("tokenizer_fallback", error_type=type(exc).__name__.lower())
                response = await client.post(f"{self.base_url}{endpoint}", json=payload)
                response.raise_for_status()
            return ""
        self._operation(warm, threading.Event(), 90)

    def _operation(self, factory, cancel, timeout):
        local_cancel = threading.Event()
        with self._lifecycle_lock:
            if self._closed:
                return ""
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

    def release_async(self, on_done: Callable[[bool], None] | None = None) -> threading.Thread:
        # Retire this backend before the worker starts. Delayed startup probes
        # and queued requests must not reload it after the user releases it.
        with self._lifecycle_lock:
            self._closed = True
            for event in self._active:
                event.set()
        def release():
            ok = False
            try:
                with self._network_lock:
                    response = self.client.post(f"{self.base_url}/api/generate",
                                                json={"model": self.model, "keep_alive": 0}, timeout=2)
                    response.raise_for_status()
                    ok = True
                    log_event("model_released", shown=True)
            except Exception as exc:
                log_event("model_release_failed", error_type=type(exc).__name__.lower())
            finally:
                self.close()
                if on_done is not None:
                    on_done(ok)
        thread = threading.Thread(target=release, daemon=True)
        thread.start()
        return thread

    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str:
        if not can_continue(request.draft):
            return ""
        if self.completion_family:
            return self._suggest_native(request, emit, cancel)
        payload = {"model": self.model, "stream": True, "keep_alive": -1,
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
        async def complete():
            prepared = fit_request(request, self.token_counter)
            return await complete_request(stream, prepared)
        final = self._operation(complete, cancel, self.request_timeout)
        if cancel.is_set() or not final:
            return ""
        if final:
            emit(final)
        return final

    def _native_payload(self, request: SuggestionRequest, *, stream: bool = True) -> dict:
        return {"model": self.model, "stream": stream, "raw": True, "keep_alive": -1,
                "prompt": build_native_prompt(request),
                "options": {"temperature": 0, "num_predict": NATIVE_TOKENS,
                            "num_ctx": CONTEXT_TOKENS, "repeat_penalty": 1.0,
                            "stop": list(NATIVE_STOPS)}}

    def _suggest_native(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str:
        payload = self._native_payload(request)

        async def stream(prompt, budget):
            raw = ""
            payload["prompt"] = prompt
            payload["options"]["num_predict"] = budget
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         timeout=self.request_timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise RuntimeError(chunk["error"])
                        raw += chunk.get("response", "")
                        if chunk.get("done") or native_segment(raw) is not None:
                            break
            return raw

        async def complete():
            prepared = fit_request(request, self.token_counter, native=True)
            return await complete_native_request(stream, prepared)
        final = self._operation(complete, cancel, self.request_timeout)
        if cancel.is_set() or not final:
            return ""
        emit(final)
        return final

    def close(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
            for event in self._active:
                event.set()
        self.client.close()


def make_backend(config: AppConfig) -> OllamaBackend:
    return OllamaBackend(config.ollama_url, config.ollama_model,
                         request_timeout=config.request_timeout_seconds)
