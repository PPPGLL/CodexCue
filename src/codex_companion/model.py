from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urlparse

import httpx

from .config import AppConfig, get_cloud_key
from .sessions import Message

Emit = Callable[[str], None]


@dataclass(frozen=True)
class SuggestionRequest:
    messages: list[Message]
    draft: str


class SuggestionBackend(Protocol):
    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str: ...


SYSTEM_PROMPT = (
    "你是输入框续写器。根据之前的对话，续写用户正在输入给助手的消息。"
    "优先承接最近对话中的具体对象、约束和未解决的问题；草稿换了话题时以草稿为准。"
    "只生成接在草稿末尾的文字；回答的第一个字就是续写的第一个字。"
    "只补全一个短语或分句，最多20个汉字。绝不输出草稿、标签、引号、解释、换行或重复句子。"
    "不要替助手回答问题；如果草稿像完整句子，继续写一个相关的具体要求。"
)

COMPLETION_PROMPT = (
    "你在补全用户尚未发送的输入内容。先看最近的用户消息和助手最终回复，"
    "优先承接其中的具体对象、约束和未解决的问题；若草稿转了话题，就以草稿为准。"
    "继续最后一条未结束的 user 消息，保持用户语气。"
    "只续写一个短语或分句，最多20个汉字；草稿像完整句子时也可补一小段相关要求。"
    "不要回答用户，不要重复草稿或续写过的内容。/no_think"
)

FOLLOWUP_PROMPT = (
    "你正在替用户本人续写给助手的输入。根据最近对话，只输出应追加在草稿末尾的文字，"
    "保持用户的请求语气；不要作为助手回答或反问用户，不要称用户为‘您’。"
    "不要重复草稿或虚构已发生的事实。草稿已带标点时，直接续写一小段相关的具体要求；"
    "草稿没有标点时，可以先补合适的标点。不要只输出标点。"
    "最多20个汉字，不要标签、解释、引号或换行。"
    "例如草稿‘为什么点击后没有反应？’可续写‘请帮我排查点击事件是否触发’。"
)


def build_completion_prompt(request: SuggestionRequest) -> str:
    """Leave the last user turn open so the model continues its text, not answers it."""
    def safe(value: str) -> str:
        return value.replace("<|", "< |")

    parts = [f"<|im_start|>system\n{COMPLETION_PROMPT}<|im_end|>"]
    for message in request.messages:
        if message.text.strip():
            parts.append(f"<|im_start|>{message.role}\n{safe(message.text)}<|im_end|>")
    parts.append(f"<|im_start|>user\n{safe(request.draft)}")
    return "\n".join(parts)


def build_followup_prompt(request: SuggestionRequest, ending: str) -> str:
    """Ask for a useful suffix when open-turn completion ended at punctuation."""
    def safe(value: str) -> str:
        return value.replace("<|", "< |")

    parts = [f"<|im_start|>system\n{FOLLOWUP_PROMPT}<|im_end|>"]
    for message in request.messages:
        if message.text.strip():
            parts.append(f"<|im_start|>{message.role}\n{safe(message.text)}<|im_end|>")
    parts.append(
        f"<|im_start|>user\n草稿：{safe(request.draft + ending)}\n"
        "只输出继续追加在草稿末尾的文字。<|im_end|>"
    )
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def build_messages(request: SuggestionRequest) -> list[dict[str, str]]:
    history = [
        {"role": message.role, "content": message.text}
        for message in request.messages
        if message.text.strip()
    ]
    draft = request.draft[-1000:]
    if draft:
        instruction = f"草稿：{draft}\n续写："
    else:
        instruction = "草稿为空。直接写一条简短的用户消息。"
    return [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": instruction}]


def build_followup_messages(request: SuggestionRequest, ending: str) -> list[dict[str, str]]:
    messages = build_messages(SuggestionRequest(request.messages, request.draft + ending))
    messages[0] = {"role": "system", "content": FOLLOWUP_PROMPT}
    return messages


def normalize_suggestion(raw: str, draft: str, limit: int = 120) -> str:
    if "<think>" in raw and "</think>" not in raw:
        return ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = raw.strip().splitlines()[0] if raw.strip() else ""
    raw = re.sub(r"^(?:[-*•]\s*|\d+[.)、]\s*)", "", raw).strip()
    raw = raw.strip(" \t\"'“”‘’")
    if draft and raw.startswith(draft):
        raw = raw[len(draft) :]
    elif draft and draft.startswith(raw):
        return ""  # A partial echo of the draft is not a continuation.
    elif draft:
        for count in range(min(len(draft), len(raw)), 1, -1):
            if draft.endswith(raw[:count]):
                raw = raw[count:]
                break
    return raw[:limit]


def readable(text: str, final: bool = False) -> bool:
    if not text:
        return False
    if final:
        return True
    return len(text.strip()) >= 4


def punctuation_only(text: str) -> bool:
    return bool(text) and all(char in "。！？!?，,；;：:.…" for char in text)


def _local_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Ollama must use a local HTTP address")
    return base_url.rstrip("/")


class OllamaBackend:
    def __init__(self, base_url: str, model: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = _local_url(base_url)
        self.model = model
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

    def _uses_qwen_prompt(self) -> bool:
        family = self.model.split("/")[-1].split(":")[0].lower()
        return family.startswith(("qwen3", "qwen2.5"))

    def warm(self) -> None:
        qwen = self._uses_qwen_prompt()
        payload = {"model": self.model, "keep_alive": -1, "stream": False,
                   "think": False, "options": {"num_predict": 1, "num_ctx": 3072}}
        if qwen:
            payload.update(prompt=build_completion_prompt(SuggestionRequest([], "你好")), raw=True)
        else:
            payload["messages"] = build_messages(SuggestionRequest([], "你好"))
        response = self.client.post(
            f"{self.base_url}/api/{'generate' if qwen else 'chat'}",
            json=payload, timeout=90,
        )
        response.raise_for_status()

    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str:
        if not request.draft.strip():
            return ""
        qwen = self._uses_qwen_prompt()

        def generate(prompt: str | list[dict[str, str]], draft: str,
                     stream_updates: bool) -> str:
            payload = {"model": self.model, "stream": True, "keep_alive": -1,
                       "think": False,
                       "options": {"temperature": 0.2, "num_predict": 24, "num_ctx": 3072,
                                   "repeat_penalty": 1.15, "repeat_last_n": 64,
                                   "stop": ["\n", "<|im_end|>", "<|im_start|>"]}}
            if qwen:
                payload.update(prompt=prompt, raw=True)
            else:
                payload["messages"] = prompt
            raw = ""
            shown = ""
            endpoint = "generate" if qwen else "chat"
            with self.client.stream("POST", f"{self.base_url}/api/{endpoint}", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancel.is_set():
                        return ""
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if qwen:
                        raw += chunk.get("response", "")
                    else:
                        raw += chunk.get("message", {}).get("content", "")
                    current = normalize_suggestion(raw, draft)
                    if (stream_updates and current != shown and readable(current)
                            and not punctuation_only(current)):
                        shown = current
                        emit(current)
                    if chunk.get("done"):
                        break
            return normalize_suggestion(raw, draft)

        first_prompt = build_completion_prompt(request) if qwen else build_messages(request)
        first = generate(first_prompt, request.draft, True)
        if cancel.is_set():
            return ""
        if not first or punctuation_only(first):
            ending = first if punctuation_only(first) else ""
            followup_prompt = (build_followup_prompt(request, ending) if qwen else
                               build_followup_messages(request, ending))
            followup = generate(followup_prompt, request.draft + ending, False)
            if cancel.is_set():
                return ""
            if ending and followup.startswith(ending):
                followup = followup[len(ending):]
            final = ending + followup if followup and not punctuation_only(followup) else ""
        else:
            final = first
        if readable(final, final=True):
            emit(final)
        return final

    def close(self) -> None:
        self.client.close()


class OpenAICompatibleBackend:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Cloud endpoints must use HTTPS")
        if not parsed.netloc or not model or not api_key:
            raise ValueError("Cloud endpoint, model, and API key are required")
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.client = httpx.Client(transport=transport, timeout=httpx.Timeout(35.0, connect=5.0))

    def suggest(self, request: SuggestionRequest, emit: Emit, cancel: threading.Event) -> str:
        if not request.draft.strip():
            return ""
        raw = ""
        shown = ""
        payload = {
            "model": self.model,
            "messages": build_messages(request),
            "stream": True,
            "temperature": 0.2,
            "max_tokens": 24,
        }
        with self.client.stream(
            "POST",
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if cancel.is_set():
                    return ""
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                delta = json.loads(data).get("choices", [{}])[0].get("delta", {})
                raw += delta.get("content") or ""
                current = normalize_suggestion(raw, request.draft)
                if current != shown and readable(current):
                    shown = current
                    emit(current)
        final = normalize_suggestion(raw, request.draft)
        if final != shown and readable(final, final=True):
            emit(final)
        return final

    def close(self) -> None:
        self.client.close()


def make_backend(config: AppConfig) -> SuggestionBackend:
    if config.backend == "ollama":
        return OllamaBackend(config.ollama_url, config.ollama_model)
    if config.backend == "cloud":
        api_key = get_cloud_key(config.cloud_base_url)
        return OpenAICompatibleBackend(config.cloud_base_url, config.cloud_model, api_key)
    raise ValueError(f"Unknown backend: {config.backend}")
