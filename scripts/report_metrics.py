"""Summarize numeric runtime events; never export draft or conversation contents."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


def distribution(values):
    values = sorted(values)
    def percentile(p):
        return values[max(0, math.ceil(len(values) * p) - 1)] if values else None
    return {"count": len(values), "p50_ms": percentile(.5), "p95_ms": percentile(.95),
            "max_ms": max(values) if values else None}


def summarize(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max((i for i, line in enumerate(lines) if " INFO app_started" in line), default=0)
    events = []
    for line in lines[start:]:
        match = re.search(r" INFO ([a-z_]+)(.*)$", line)
        if match:
            events.append((match[1], dict(re.findall(r"([a-z_]+)=([a-zA-Z0-9_]+)", match[2]))))
    def times(event, shown=False):
        return [int(v["latency_ms"]) for e, v in events if e == event and "latency_ms" in v
                and (not shown or v.get("shown") == "True")]
    shown = sum(e == "request_finished" and v.get("shown") == "True" for e, v in events)
    accepted = sum(e == "suggestion_accepted" for e, _ in events)
    return {"scope": "latest_logged_process_session", "input_to_popup": distribution(times("suggestion_latency", True)),
            "backend": distribution(times("request_finished")), "uia_read": distribution(times("draft_read")),
            "ui_stalls_over_100ms": len(times("main_loop_delay")),
            "suggestions_shown": shown, "tab_accepts": accepted,
            "observed_acceptance_rate": accepted / shown if shown else None,
            "request_failures": sum(e == "request_failed" for e, _ in events),
            "semantic_error_rate": None,
            "notes": "Acceptance is a usage count, not a semantic-quality score. Missing observations remain null."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(summarize(args.log), ensure_ascii=False, indent=2)
    print(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()
