"""Mechanical output checks on synthetic drafts, not a semantic quality judge."""
from __future__ import annotations

import re
import threading

from .model import SuggestionBackend, SuggestionRequest, repeats_input
from .sessions import Message


def check_output_contract(backend: SuggestionBackend) -> list[dict]:
    # Preserve the original inputs. User voice, relevance and scope need review
    # of the complete draft + suffix, not a list of expected topic words.
    background = [
        Message("assistant", "请在收到粗略修改意见后自动补充2到4个具体要求，聚焦检查什么、改善什么和如何验收，"
                "确保补充内容衔接原句，控制在80到160个中文字之间。"),
        Message("user", "刚才那段生成结果不合适。"),
    ]
    drafts = [
        "你好", "测试", "我希望你", "我觉得",
        "这个图表颜色分不清，调整一下",
        "按钮点击后没有反馈，改一下",
        "导出的报告缺没缺内容看不出来，改一下",
        "付款返回之后不知道该往哪里走，理顺一下",
        "只分析不要改动，看看结算流程哪些地方不顺",
        "接下来检查网络请求的", "这个结果不对，应该",
        "请检查配置文件 config.",
        "我们正在审查一个程序的启动流程。" * 5 + "请先帮我检查初始化的",
    ]
    rows = []
    for draft in drafts:
        emitted = []
        request = SuggestionRequest(background, draft)
        suffix = backend.suggest(request, emitted.append, threading.Event())
        checks = {
            "nonempty": bool(suffix.strip()),
            "substantive": any(c.isalnum() for c in suffix),
            "emitted_matches": emitted == ([suffix] if suffix else []),
            "no_protocol": not bool(re.search(r'"(?:continuation|anchor)"\s*:|<\||<｜|<fim_|<think>', suffix)),
            "no_copied_input": not repeats_input(suffix, request),
            "short_output": len(suffix) <= 80,
        }
        signals = {}
        if draft.endswith(("的", "应该", "config.")):
            signals["boundary_at_cursor"] = suffix.lstrip().startswith(("。", "，", "；", ".", ","))
        rows.append({"draft": draft, "suffix": suffix, "combined": draft + suffix,
                     "background": [[m.role, m.text] for m in background], "checks": checks,
                     "status": "PASS" if all(checks.values()) else "FAIL",
                     "scope": "mechanical_only", "diagnostic_signals": signals,
                     "semantic_review": "NOT_REVIEWED"})
    return rows
