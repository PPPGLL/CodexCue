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

from .config import AppConfig, get_cloud_key
from .completion_modes import (CompletionRoute, SuggestionText, SHORT_PROMPT,
                               ROUTE_PROMPT, ROUTE_EXAMPLES, ROUTE_SCHEMA,
                               DETAIL_COMMON, DETAIL_PROMPTS, DETAIL_EXAMPLES, REQUIREMENTS_SCHEMA,
                               ANALYSIS_ONLY_PROMPT, ANALYSIS_EXAMPLES, analysis_only,
                               parse_route, decode_requirements)
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
    # The app explicitly supplies its persisted style (auto by default).
    # Low-level callers retain the existing one-request short-completion API.
    style: str = "short"


class SuggestionBackend(Protocol):
    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str: ...


MAX_SUGGESTION_CHARS = 360
COMPLETION_TOKENS = 512
CONTEXT_TOKENS = 4096

SYSTEM_PROMPT = SHORT_PROMPT

CONTINUATION_SCHEMA = {
    "type": "object",
    "properties": {"continuation": {"type": "string"}},
    "required": ["continuation"],
    "additionalProperties": False,
}


def draft_anchor(draft: str) -> str:
    # Keep control characters out of Ollama's regex-constrained JSON string;
    # the full multiline draft remains available in the quoted input.
    return re.split(r"[\r\n\t]", draft)[-1][-48:]


def continuation_schema(draft: str) -> dict:
    # Ollama's constrained decoder copies the anchor exactly. This prevents a
    # long/repetitive draft from being paraphrased before we extract its suffix.
    literal = re.escape(draft_anchor(draft))
    return {**CONTINUATION_SCHEMA, "properties": {
        "continuation": {"type": "string", "pattern": "^" + literal + ".*$"}}}

_EXAMPLES = (
    ([], "Please check the conn", "ection settings"),
    ([], "这个函数的输出应该", "是什么类型？"),
    ([], "先不要修改文件，请先", "说明你对需求的理解，并列出需要确认的问题。"),
    ([], "我觉得", "这里还有一些可以调整的地方。"),
    ([], "我希望你", "先帮我梳理一下目前的问题。"),
    ([], "你好", "，我想请你帮我看一个问题。"),
    ([Message("user", "我正在调整设置页的布局。")], "我想", "把常用设置放得更醒目一些。"),
    ([Message("assistant", "工具按规则扩写修改意见，并控制字数。")], "现在有个问题", "需要你帮我看一下。"),
    ([Message("assistant", "生成规则：补充具体检查项、改善点与验收标准，控制总字数。")], "我觉得", "这里还有些地方可以改进。"),
)


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
    for background, draft, suffix in _EXAMPLES:
        messages.extend([
            {"role": "user", "content": _completion_input(background, draft)},
            {"role": "assistant", "content": json.dumps({"continuation": draft_anchor(draft) + suffix}, ensure_ascii=False)},
        ])
    messages.append({"role": "user", "content": _completion_input(request.messages, request.draft)})
    return messages


def can_continue(draft: str) -> bool:
    text = draft.strip()
    return bool(text) and not text.endswith(("?", "？"))


def build_route_messages(request: SuggestionRequest) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": ROUTE_PROMPT}]
    for draft, mode, focus, evidence in ROUTE_EXAMPLES:
        messages.extend([
            {"role": "user", "content": _completion_input([], draft)},
            {"role": "assistant", "content": json.dumps({"mode": mode, "focus": focus, "evidence": evidence}, ensure_ascii=False)},
        ])
    messages.append({"role": "user", "content": _completion_input(request.messages, request.draft)})
    return messages


def build_detail_messages(request: SuggestionRequest, route: CompletionRoute) -> list[dict[str, str]]:
    data = json.loads(_completion_input(request.messages, request.draft))
    data["focus"] = route.focus
    readonly = analysis_only(request.draft)
    example_draft, requirements = (ANALYSIS_EXAMPLES if readonly else DETAIL_EXAMPLES)[route.kind]
    prompt = DETAIL_COMMON + "\n" + DETAIL_PROMPTS[route.kind]
    if readonly:
        prompt += "\n" + ANALYSIS_ONLY_PROMPT
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": _completion_input([], example_draft)},
            {"role": "assistant", "content": json.dumps({"requirements": requirements}, ensure_ascii=False)},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False).replace("<|", "< |")}]


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
    for source in (SYSTEM_PROMPT, DETAIL_COMMON, *DETAIL_PROMPTS.values(),
                   request.draft, *(m.text for m in request.messages)):
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
    """Route, generate and recover within one cancellation/time budget."""
    route = CompletionRoute()
    if request.style == "auto":
        raw = await read(build_route_messages(request), ROUTE_SCHEMA, 160)
        route = parse_route(raw, request.draft, request.messages)
    if route.kind != "short":
        detail_messages = build_detail_messages(request, route)
        for attempt in range(2):
            raw = await read(detail_messages, REQUIREMENTS_SCHEMA, COMPLETION_TOKENS)
            try:
                result = decode_requirements(raw, request.draft, completion_background(request.messages), route)
                if repeats_input(result, request):
                    raise ValueError("copied_input")
                return result
            except ValueError:
                log_event("completion_retry", reason="invalid_requirements")
                if attempt == 0:
                    # Correct the violated constraint, without feeding the bad
                    # output back into context. Still under the same deadline.
                    detail_messages = build_detail_messages(request, route)
                    correction = (
                        "\n上次输出未通过格式检查，请重新生成。确保共 3 项，每项约 35 个中文字符，"
                        "总长 100–140 字；只补充当前对象的要求，不复述草稿或背景。"
                        "不要加入任何数字、耗时目标或其他未经提供的定量要求。"
                    )
                    if not re.search(r"[\u3400-\u9fff]", request.draft):
                        correction = ("\nRegenerate in the draft's language: three distinct requirements, "
                                      "35–55 words total and at most 350 characters. Do not repeat the input "
                                      "or introduce unsupported numbers, thresholds or implementation choices.")
                    detail_messages[0]["content"] += correction
        # A failed expansion still gets a short guess, instead of disappearing.
    schema = continuation_schema(request.draft)
    raw = await read(build_messages(request), schema, 256)
    suffix = decode_suggestion(raw, request.draft)
    if repeats_input(suffix, request) or not normalize_suggestion(suffix, "", limit=60):
        log_event("completion_retry", reason="invalid_short", suggestion_len=len(suffix))
        # The draft stays identical. Remove the source of the copied prose from
        # this repair request; do not feed the bad suggestion back as an example.
        raw = await read(build_messages(SuggestionRequest([], request.draft)), schema, 256)
        suffix = decode_suggestion(raw, request.draft)
        if repeats_input(suffix, request):
            log_event("completion_output", reason="repeated_after_retry", suggestion_len=0)
            return ""
    return SuggestionText(normalize_suggestion(suffix, "", limit=60))


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


class OpenAICompatibleBackend:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        *, request_timeout: float = 8.0,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Cloud endpoints must use HTTPS")
        if not parsed.netloc or not model or not api_key:
            raise ValueError("Cloud endpoint, model, and API key are required")
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.transport = transport
        self.request_timeout = request_timeout
        self.client = httpx.Client(transport=transport, timeout=httpx.Timeout(35.0, connect=5.0))

    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str:
        if not can_continue(request.draft):
            return ""
        payload = {
            "model": self.model,
            "messages": build_messages(request),
            "stream": True,
            "temperature": 0,
            "max_tokens": COMPLETION_TOKENS,
        }
        async def stream(messages, schema, budget):
            raw = ""
            payload["messages"] = messages
            payload["max_tokens"] = budget
            async with httpx.AsyncClient(transport=self.transport, timeout=self.request_timeout) as client:
                async with client.stream("POST", self.url, json=payload,
                                         headers={"Authorization": f"Bearer {self.api_key}"}) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        delta = json.loads(data).get("choices", [{}])[0].get("delta", {})
                        raw += delta.get("content") or ""
            return raw
        final = _run_cancellable(lambda: complete_request(stream, request), cancel.is_set, self.request_timeout)
        if cancel.is_set() or not final:
            return ""
        if final:
            emit(final)
        return final

    def close(self) -> None:
        self.client.close()


def make_backend(config: AppConfig) -> SuggestionBackend:
    if config.backend == "ollama":
        return OllamaBackend(config.ollama_url, config.ollama_model,
                             keep_alive_seconds=config.model_idle_seconds,
                             request_timeout=config.request_timeout_seconds)
    if config.backend == "cloud":
        api_key = get_cloud_key(config.cloud_base_url)
        return OpenAICompatibleBackend(config.cloud_base_url, config.cloud_model, api_key,
                                       request_timeout=config.request_timeout_seconds)
    raise ValueError(f"Unknown backend: {config.backend}")
