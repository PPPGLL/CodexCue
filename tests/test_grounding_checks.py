import pytest

from codex_companion.quality_checks import check_context_grounding


@pytest.mark.parametrize("suffix,expected", [
    ("，先确认原始数据和报告结构是否一致。", "PASS"),
    ("，请对照原始数据标出报告中的差异。", "PASS"),
    ("，先核对导出内容的完整性。", "PASS"),
    ("，先确认报告中哪些部分是必须保留的，再调整结构。", "FAIL"),
    ("，先核对完整性，再调整结构。", "FAIL"),
    ("，删除缺失的数据后重新导出。", "FAIL"),
    ("，让按钮颜色更明显。", "FAIL"),
])
def test_report_check_accepts_comparison_without_expanding_scope(suffix, expected):
    class Backend:
        def suggest(self, request, emit, cancel):
            emit(suffix)
            return suffix

    results = check_context_grounding(Backend())
    report = next(r for r in results if r["draft"].startswith("导出的报告"))
    assert report["status"] == expected
