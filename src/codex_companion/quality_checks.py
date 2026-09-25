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
        ("你好", "short", ()), ("测试", "short", ()),
        ("我希望你", "short", ()), ("我觉得", "short", ()),
        ("这个图表颜色分不清，调整一下", "check", ("颜色", "对比", "区分")),
        ("按钮点击后没有反馈，改一下", "optimize", ("点击", "状态", "提示", "响应")),
        ("导出的报告缺没缺内容看不出来，改一下", "acceptance", ("完整", "缺失", "遗漏", "核对")),
        ("付款返回之后不知道该往哪里走，理顺一下", "optimize", ("返回", "路径", "下一", "操作")),
        ("只分析不要改动，看看结算流程哪些地方不顺", "optimize", ("结算", "步骤", "流程", "操作")),
    ]
    rows = []
    for draft, kind, topic_words in cases:
        emitted = []
        suffix = backend.suggest(SuggestionRequest(background, draft, "auto"), emitted.append, threading.Event())
        actual_kind = getattr(suffix, "kind", "")
        count = len(getattr(suffix, "requirements", ()))
        passed = (bool(suffix) and emitted == [suffix] and actual_kind == kind
                  and not any(w in suffix for w in ("80到160", "2到4", "粗略修改意见", "粗糙修改意见",
                                                   "anchor", "continuation", "有什么我可以帮", "标准模板", "预设模块")))
        if kind == "short":
            passed = passed and len(suffix) <= 60 and count == 0
        else:
            passed = passed and 80 <= len(suffix) <= 160 and 2 <= count <= 4 and any(w in suffix for w in topic_words)
        if draft.startswith("只分析"):
            passed = passed and not any(w in suffix for w in ("修改后", "调整后", "新增", "添加"))
        rows.append({"draft": draft, "suffix": suffix, "kind": actual_kind, "requirements": count,
                     "status": "PASS" if passed else "FAIL"})
    return rows
