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
        ("按钮点击后没有反馈，改一下", ("点击", "状态", "提示", "响应")),
        ("导出的报告缺没缺内容看不出来，改一下", ("完整", "缺失", "遗漏", "核对", "对照", "一致", "差异")),
        ("付款返回之后不知道该往哪里走，理顺一下", ("返回", "路径", "下一", "操作")),
        ("只分析不要改动，看看结算流程哪些地方不顺", ("结算", "步骤", "流程", "操作", "输入", "校验", "环节")),
        ("接下来检查网络请求的", ()), ("这个结果不对，应该", ()),
        ("请检查配置文件 config.", ()),
        ("我们正在审查一个程序的启动流程。" * 5 + "请先帮我检查初始化的", ()),
    ]
    rows = []
    for draft, topic_words in cases:
        emitted = []
        suffix = backend.suggest(SuggestionRequest(background, draft), emitted.append, threading.Event())
        passed = (bool(suffix) and emitted == [suffix]
                  and not any(w in suffix for w in ("80到160", "2到4", "粗略修改意见", "粗糙修改意见",
                                                   "anchor", "continuation", "有什么我可以帮", "标准模板", "预设模块")))
        if not topic_words:
            passed = passed and len(suffix) <= 60
            if draft.endswith(("的", "应该", "config.")):
                passed = passed and not suffix.startswith(("。", "，", "；", ".", ","))
        else:
            # One useful requirement per Tab. The user can accept another step;
            # the former 60-character minimum encouraged unnecessary expansion.
            passed = passed and len(suffix) <= 80 and any(w in suffix for w in topic_words)
            passed = passed and suffix.endswith(("。", "？", "！")) and not any(c.isdigit() for c in suffix)
        if draft.startswith("只分析"):
            passed = passed and not any(w in suffix for w in ("修改后", "调整后", "新增", "添加"))
        if draft.startswith("导出的报告"):
            # Comparing source/report consistency is a valid completeness check.
            # Reorganizing the report is a different task, even if the output
            # happens to include a completeness keyword.
            passed = passed and not any(w in suffix for w in (
                "调整结构", "修改结构", "重组", "重构", "删除", "移除"))
        rows.append({"draft": draft, "suffix": suffix,
                     "status": "PASS" if passed else "FAIL"})
    return rows
