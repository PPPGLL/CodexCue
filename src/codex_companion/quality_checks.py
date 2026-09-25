"""Synthetic model regressions; no Codex task logs or user drafts are read."""
from __future__ import annotations

import threading

from .model import SuggestionBackend, SuggestionRequest
from .sessions import Message


def check_context_grounding(backend: SuggestionBackend) -> list[dict]:
    # Deliberately misleading quoted instructions, based on the old prompt.
    background = [
        Message("assistant", "请在收到粗略修改意见后自动补充2到4个具体要求，聚焦检查什么、改善什么和如何验收，"
                "确保补充内容衔接原句，控制在80到160个中文字之间。"),
        Message("user", "刚才那段生成结果不合适。"),
    ]
    cases = [
        ("你好", ()), ("测试", ()), ("我希望你", ()), ("我觉得", ()),
        ("这个图表颜色分不清，调整一下", ("颜色", "对比", "区分")),
        ("这段说明写得太绕了，精简一下", ("原意", "句", "表达", "文字")),
        ("按钮点击后没有反馈，改一下", ("点击", "状态", "提示", "响应")),
    ]
    rows = []
    for draft, topic_words in cases:
        emitted = []
        suffix = backend.suggest(SuggestionRequest(background, draft), emitted.append, threading.Event())
        if not topic_words:
            passed = not suffix and not emitted
        else:
            passed = (bool(suffix) and emitted == [suffix] and any(w in suffix for w in topic_words)
                      and not any(w in suffix for w in ("80到160", "2到4", "粗略修改意见",
                                                       "粗糙修改意见", "anchor", "continuation")))
        rows.append({"draft": draft, "suffix": suffix, "status": "PASS" if passed else "FAIL"})
    return rows
