import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from codex_companion.model import (SuggestionRequest, complete_request, decode_suggestion,
                                   draft_anchor, normalize_suggestion)
from codex_companion.sessions import Message
from codex_companion.state import EditorSnapshot, KEYBOARD_QUIET_SECONDS


@pytest.mark.parametrize("suffix", ["。", ".", "？！！", "，\n", "…", " \t"])
def test_punctuation_alone_is_not_a_suggestion(suffix):
    assert normalize_suggestion(suffix, "") == ""


@pytest.mark.parametrize("draft,suffix", [
    ("先核对结果。", "请同时检查失败和取消的情况。"),
    ("修好了吗？", "请说明验证过哪些场景。"),
    ("Please check the configu", "ration file."),
    ("Before testing, please", " check the setup."),
    ("数值是", "3.14。"),
])
def test_real_content_and_cursor_spacing_are_preserved(draft, suffix):
    raw = json.dumps({"continuation": draft_anchor(draft) + suffix})
    assert decode_suggestion(raw, draft) == suffix


@pytest.mark.parametrize("repaired", [False, True])
def test_punctuation_repair_keeps_task_facts_and_is_bounded(repaired):
    calls = []
    draft = "按这个结果继续。"
    async def read(messages, _schema, _budget):
        calls.append(json.loads(messages[-1]["content"]))
        suffix = "请核对 CSV 文件是否完整。" if repaired and len(calls) == 2 else "。"
        return json.dumps({"continuation": draft_anchor(draft) + suffix})
    result = asyncio.run(complete_request(read, SuggestionRequest([Message("user", "只导出 CSV。")], draft)))
    assert len(calls) == 2
    assert calls[1]["background"] == calls[0]["background"] != []
    assert result == ("请核对 CSV 文件是否完整。" if repaired else "")


def test_no_artificial_wait_still_protects_unconfirmed_edits_and_ime():
    from dataclasses import replace
    snapshot = EditorSnapshot(True, False, False, False, True, False, "draft", True,
                              123, 123, True, 0, True)
    assert KEYBOARD_QUIET_SECONDS == 0
    ready = snapshot
    assert ready.rejection() == "ready"
    assert replace(ready, composing=True).rejection() == "ime_composing"
    assert replace(ready, foreground=456).rejection() == "focus_changed"
    assert replace(ready, pending_activity=True).rejection() == "draft_unconfirmed"


def benchmark_module():
    return script_module("benchmark_completion")


def script_module(name):
    path = Path(__file__).resolve().parents[1] / f"scripts/{name}.py"
    spec = importlib.util.spec_from_file_location("completion_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_penalizes_missing_output_and_includes_its_latency():
    bench = benchmark_module()
    cases = [{"id": "a", "draft": "请检查", "no_boundary_start": True},
             {"id": "b", "draft": "已检查。"}]
    rows = [{"expect_empty": False, "suffix": suffix, "backend_ms": ms,
             "estimated_trigger_plus_backend_ms": ms + 150, "generation_requests": attempts,
             "attempts": [], "checks": bench.assess(case, suffix)}
            for case, suffix, ms, attempts in zip(cases, ["。下一步", ""], [200, 8000], [1, 2])]
    summary = bench.summarize(rows)
    assert summary["case_pass_rate"] == 0
    assert summary["backend_ms"]["p95"] == 8000
    assert summary["backend_ms_shown"]["n"] == 1
    assert summary["retry_rate"] == .5
    assert not bench.assess(cases[0], ".")["substantive"]


def test_benchmark_freezes_disjoint_cases_and_covers_context_changes():
    from codex_companion.completion_prompt import EXAMPLES
    bench = benchmark_module()
    cases = json.loads(bench.DATASET.read_text(encoding="utf-8"))["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert {c["split"] for c in cases} == {"dev", "holdout"}
    assert not {c["draft"] for c in cases} & {e[1] for e in EXAMPLES}
    paired = [c for c in cases if c["category"] == "context"]
    assert len(paired) >= 8
    assert all("messages" in c and "contains_any" in c for c in paired)


def test_review_scoring_counts_preferences_and_rejects_incomplete_reviews():
    scorer = script_module("score_completion_review")
    dimensions = ("accuracy", "fluency", "context", "usefulness")
    row = {"case": "one", "scores": {v: {d: n for d in dimensions} for v, n in (("A", 2), ("B", 1))},
           "preferred": "A", "reason": "A finishes the word; B changes the topic."}
    review = {"reviewer": "Synthetic test reviewer", "cases": [row]}
    key = {"mapping": {"one": {"A": "after", "B": "before"}}}
    assert scorer.score(review, key)["preferences"]["after"] == 1
    for invalid in (None, True, -1, 3):
        row["scores"]["B"]["accuracy"] = invalid
        with pytest.raises(ValueError, match="score"):
            scorer.score(review, key)
    row["scores"]["B"]["accuracy"] = 1
    review["cases"].append(row)
    with pytest.raises(ValueError, match="exactly once"):
        scorer.score(review, key)


def test_comparison_rejects_mismatched_or_duplicate_samples():
    compare = script_module("compare_completion_benchmarks").compare
    report = {"dataset_sha256": "a", "harness_sha256": "b", "repeat": 1, "seed": 2,
              "results": [{"case": "one", "repeat": 0}]}
    with pytest.raises(ValueError, match="dataset_sha256"):
        compare(report, {**report, "dataset_sha256": "changed"})
    with pytest.raises(ValueError, match="nonduplicate"):
        compare(report, {**report, "results": report["results"] * 2})
