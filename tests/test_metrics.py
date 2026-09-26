import importlib.util
from pathlib import Path


def test_runtime_metrics_keep_missing_observations_unknown_and_scope_last_run(tmp_path):
    spec = importlib.util.spec_from_file_location("runtime_metrics", Path(__file__).resolve().parents[1] / "scripts/report_metrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "events.log"
    path.write_text("INFO suggestion_accepted generation=1\n"
                    "2026-09-25 INFO app_started\n"
                    "2026-09-25 INFO suggestion_latency latency_ms=450 shown=True\n"
                    "2026-09-25 INFO request_finished latency_ms=100 shown=True\n"
                    "2026-09-25 INFO suggestion_accepted generation=2\n", encoding="utf-8")
    result = module.summarize(path)
    assert result["tab_accepts"] == 1
    assert result["observed_acceptance_rate"] == 1
    assert result["input_to_popup"]["p95_ms"] == 450
    assert result["uia_read"]["p95_ms"] is None
    assert result["semantic_error_rate"] is None
