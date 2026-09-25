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
    "你正在续写用户本人尚未发送的消息。你是这段文字的作者，不是回答它的助手。"
    "历史对话只用于理解指代和主题；当前草稿优先，不从历史中虚构新要求或事实。"
    "只输出能直接接在草稿末尾的最短自然后缀，保持语言、语气和标点。"
    "优先补完当前短语或句子，不主动另起一句；已经完整且无需续写时输出空内容。"
    "不要重复草稿，不要标签、引号、解释、换行，也不要回答草稿里的问题。"
    "\n示例：草稿：请把插入快捷键改成；续写：Tab"
    "\n示例：草稿：补全框应该只显示；续写：需要追加的文字"
)


def build_completion_prompt(request: SuggestionRequest) -> str:
    """Give Qwen a suffix-only answer slot instead of an open conversation turn."""
    def safe(value: str) -> str:
        return value.replace("<|", "< |")

    history = "\n".join(
        f"{message.role}: {safe(message.text)}"
        for message in request.messages if message.text.strip()
    )
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n<历史对话>\n{history}\n</历史对话>\n"
        f"请续写当前草稿，只输出后缀。\n草稿：{safe(request.draft[-1000:])}"
        "<|im_end|>\n<|im_start|>assistant\n"
    )


def build_messages(request: SuggestionRequest) -> list[dict[str, str]]:
    history = [
        {"role": message.role, "content": message.text}
        for message in request.messages
        if message.text.strip()
    ]
    draft = request.draft[-1000:]
    instruction = f"请续写当前草稿，只输出后缀。\n草稿：{draft}\n续写："
    return [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": instruction}]


def normalize_suggestion(raw: str, draft: str, limit: int = 120) -> str:
    if "<think>" in raw and "</think>" not in raw:
        return ""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = raw.splitlines()[0].rstrip() if raw.strip() else ""
    raw = re.sub(r"^(?:[-*•]\s*|\d+[.)、]\s*)", "", raw)
    raw = raw.strip("\"'“”‘’")
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

        def generate(prompt: str | list[dict[str, str]], draft: str) -> str:
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
                    if current != shown and readable(current):
                        shown = current
                        emit(current)
                    if chunk.get("done"):
                        break
            final = normalize_suggestion(raw, draft)
            if final != shown and readable(final, final=True):
                emit(final)
            return final

        prompt = build_completion_prompt(request) if qwen else build_messages(request)
        final = generate(prompt, request.draft)
        if cancel.is_set():
            return ""
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
