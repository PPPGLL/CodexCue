"""Count with the installed Qwen vocabulary; never download tokenizer assets."""
from __future__ import annotations

from functools import lru_cache

import tiktoken


QWEN_PATTERN = r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"


class TokenCounter:
    def __init__(self, encoding: tiktoken.Encoding | None = None) -> None:
        self.encoding = encoding
        # Per-backend cache: history remains unchanged across draft edits. The
        # instance owns the cache so switching models cannot reuse token counts.
        self.count = lru_cache(maxsize=128)(self._count)

    @classmethod
    def from_model_info(cls, info: dict) -> TokenCounter:
        if (info.get("tokenizer.ggml.model") != "gpt2"
                or info.get("tokenizer.ggml.pre") != "qwen2"):
            raise ValueError("Unsupported tokenizer; use the UTF-8 upper bound")
        tokens = info["tokenizer.ggml.tokens"]
        types = info["tokenizer.ggml.token_type"]
        if len(tokens) != len(types) or len(tokens) < 256:
            raise ValueError("Incomplete model vocabulary")
        byte_values = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
        chars = byte_values[:]
        extra = 0
        for value in range(256):
            if value not in byte_values:
                byte_values.append(value)
                chars.append(256 + extra)
                extra += 1
        byte_decoder = dict(zip(map(chr, chars), byte_values))
        ranks = {bytes(byte_decoder[c] for c in token): i
                 for i, (token, kind) in enumerate(zip(tokens, types)) if kind == 1}
        if not all(bytes([b]) in ranks for b in range(256)):
            raise ValueError("Tokenizer does not cover every byte")
        return cls(tiktoken.Encoding(name="codexcue-installed-qwen", pat_str=QWEN_PATTERN,
                                     mergeable_ranks=ranks, special_tokens={}))

    def _count(self, text: str) -> int:
        if self.encoding is None:
            # Conservative fallback for unsupported/temporarily unavailable
            # tokenizers. Never guess that a Chinese character is one token.
            return len(text.encode("utf-8"))
        return len(self.encoding.encode_ordinary(text))

    def clip(self, text: str, budget: int, *, tail: bool = False) -> str:
        if budget <= 0:
            return ""
        if self.count(text) <= budget:
            return text
        if self.encoding is None:
            data = text.encode("utf-8")
            return (data[-budget:] if tail else data[:budget]).decode("utf-8", errors="ignore")
        tokens = self.encoding.encode_ordinary(text)
        return self.encoding.decode(tokens[-budget:] if tail else tokens[:budget], errors="ignore")

    def chat_tokens(self, messages: list[dict[str, str]]) -> int:
        # Count content exactly for supported installed tokenizers, and reserve
        # template framing, the assistant prefix and non-thinking markers.
        # This is a safe prompt budget, not an exact server billing-token count.
        return sum(self.count(m["content"]) + 12 for m in messages) + 128
