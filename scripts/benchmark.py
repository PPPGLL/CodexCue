"""Backend-only latency probe; desktop keystroke-to-popup timing needs manual UI measurement."""
from __future__ import annotations

import argparse
import math
import statistics
import threading
import time

from codex_companion.config import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL
from codex_companion.model import OllamaBackend, SuggestionRequest
from codex_companion.sessions import Message
from codex_companion.state import KEYBOARD_QUIET_SECONDS

SAMPLES = [
    "请帮我解释", "这个错误可能是", "我想把它改成", "能给我一个例子", "先检查一下",
    "请总结刚才的", "这里的性能瓶颈", "如果改用本地模型", "请只修改", "为什么会出现",
    "帮我写一个测试", "把输出改成中文", "接下来应该", "这个函数需要", "请比较两个方案",
    "能否保持原有", "请检查边界条件", "这里不要自动", "请给出最短实现", "帮我定位问题",
    "请补充文档", "这个结果意味着", "如何避免重复", "请给出完整命令", "现在重新运行",
    "请验证是否生效", "我想继续讨论", "把建议写成", "请先看日志", "这段代码可以",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()
    if args.runs < 30:
        parser.error("at least 30 runs are required")
    backend = OllamaBackend(args.url, args.model)
    try:
        _, installed = backend.available()
        if not installed:
            parser.error("model not installed; install explicitly via settings")
        backend.warm()
        values = []
        for index in range(args.runs):
            start = time.perf_counter()
            # Include the configured quiet period, but not UIA/Qt overhead.
            time.sleep(KEYBOARD_QUIET_SECONDS)
            backend.suggest(SuggestionRequest([Message("assistant", "好的，我来帮你。")],
                                              SAMPLES[index % len(SAMPLES)]),
                            lambda _: None,
                            threading.Event())
            values.append(time.perf_counter() - start)
            print(f"{index + 1:02d}: {values[-1]:.3f}s", flush=True)
        ordered = sorted(values)
        p95 = ordered[math.ceil(.95 * len(ordered)) - 1]
        print(f"Quiet-period + backend median={statistics.median(values):.3f}s p95={p95:.3f}s max={max(values):.3f}s n={len(values)}")
        print("This is NOT a desktop last-key-to-stable-popup measurement.")
    finally:
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
