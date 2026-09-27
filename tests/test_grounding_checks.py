import pytest

from codex_companion.quality_checks import check_output_contract


def run(suffix, *, emit=True):
    class Backend:
        def suggest(self, request, callback, cancel):
            if emit and suffix:
                callback(suffix)
            return suffix
    return check_output_contract(Backend())


@pytest.mark.parametrize("suffix", ["", "。", "？！！", '{"continuation":"原文"}', "<|im_start|>assistant"])
def test_missing_text_and_protocol_output_fail_mechanical_checks(suffix):
    assert all(row["status"] == "FAIL" for row in run(suffix))


def test_emitted_text_must_match_the_returned_suffix():
    assert all(not row["checks"]["emitted_matches"] for row in run("更自然一点。", emit=False))


def test_copying_the_quoted_generation_policy_is_detected():
    rows = run("请在收到粗略修改意见后自动补充2到4个具体要求，聚焦检查什么、改善什么和如何验收。")
    assert all(not row["checks"]["no_copied_input"] for row in rows)


@pytest.mark.parametrize("suffix", [
    "流程逻辑。",  # Relevant, despite missing the former 'path' keyword.
    "，让按钮颜色更明显。",  # Off-topic: a format check cannot judge this.
    "，不需要新增功能。",  # Negation must not be rejected for the word 'add'.
    "，先重构整个程序。",  # Scope expansion needs semantic review.
])
def test_mechanical_pass_never_claims_semantic_relevance_or_scope(suffix):
    row = next(r for r in run(suffix) if r["draft"].startswith("付款返回"))
    assert row["status"] == "PASS"
    assert row["scope"] == "mechanical_only"
    assert row["semantic_review"] == "NOT_REVIEWED"
    assert row["combined"] == row["draft"] + suffix


def test_cursor_boundary_is_flagged_for_semantic_review():
    rows = run("。再看看。")
    row = next(r for r in rows if r["draft"].endswith("应该"))
    assert row["diagnostic_signals"]["boundary_at_cursor"]
    assert row["status"] == "PASS"
    assert row["semantic_review"] == "NOT_REVIEWED"
