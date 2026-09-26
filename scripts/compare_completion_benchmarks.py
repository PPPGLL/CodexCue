"""Compare matched benchmark runs and export a blinded semantic-review sheet."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random


def compare(before: dict, after: dict) -> str:
    for key in ("dataset_sha256", "harness_sha256", "repeat", "seed"):
        if before[key] != after[key]:
            raise ValueError(f"Cannot compare different {key}")
    def ids(report):
        pairs = [(x["case"], x["repeat"]) for x in report["results"]]
        if not pairs or len(pairs) != len(set(pairs)):
            raise ValueError("Results must contain nonduplicate case/repeat pairs")
        return set(pairs)
    if ids(before) != ids(after):
        raise ValueError("Cannot compare different case sets")
    lines = ["# Completion benchmark comparison", "",
             "Warm local model, synthetic drafts. Rule checks are regression signals, not a semantic accuracy or user acceptance score.", "",
             "| Metric | Before | After |", "|---|---:|---:|"]
    fields = [
        ("Rule-case pass rate", lambda r: f"{r['summary']['case_pass_rate']:.1%}"),
        ("Nonempty output rate", lambda r: f"{r['summary']['shown_rate']:.1%}"),
        ("Keyboard quiet period (ms)", lambda r: f"{r['keyboard_quiet_ms']:.0f}"),
        ("Warm backend p50 (ms)", lambda r: r['summary']['backend_ms']['p50']),
        ("Warm backend p95 (ms)", lambda r: r['summary']['backend_ms']['p95']),
        ("Estimated trigger + backend p50 (ms)", lambda r: r['summary']['estimated_trigger_plus_backend_ms']['p50']),
        ("Estimated trigger + backend p95 (ms)", lambda r: r['summary']['estimated_trigger_plus_backend_ms']['p95']),
        ("Retry rate", lambda r: f"{r['summary']['retry_rate']:.1%}"),
        ("Raw first responses containing only punctuation", lambda r: r['summary']['raw_first_punctuation_only']),
    ]
    lines += [f"| {name} | {fn(before)} | {fn(after)} |" for name, fn in fields]
    lines += ["", "The estimates exclude UI Automation and Qt display time. Backend timings include empty, failed and retried requests; raw model output is retained.", "",
              "| Split | Before | After |", "|---|---:|---:|"]
    for split in before["by_split"]:
        lines.append(f"| {split} | {before['by_split'][split]['case_pass_rate']:.1%} | {after['by_split'][split]['case_pass_rate']:.1%} |")
    lines += ["", f"Before model: `{before['model']['name']}` / `{before['model']['digest']}`.",
              f"After model: `{after['model']['name']}` / `{after['model']['digest']}`.",
              f"Dataset SHA256: `{before['dataset_sha256']}`.", "",
              "Review the paired outputs before deciding which prompt is better. Fixed word checks can flag a valid negation, and can miss a made-up claim followed by a question. Repeated deterministic runs measure timing stability, not independent quality samples.", ""]
    return "\n".join(lines)


def blind_sheet(before: dict, after: dict, seed: int = 41) -> tuple[dict, dict]:
    # One sample per case: repetitions are correlated, not new review examples.
    left = {r["case"]: r for r in before["results"] if r["repeat"] == 0 and not r["expect_empty"]}
    right = {r["case"]: r for r in after["results"] if r["repeat"] == 0 and not r["expect_empty"]}
    if left.keys() != right.keys():
        raise ValueError("Review case sets differ")
    rng = random.Random(seed)
    order = list(left)
    rng.shuffle(order)
    rows, key = [], {}
    for case in order:
        versions = ["before", "after"]
        rng.shuffle(versions)
        choices = {"before": left[case]["suffix"], "after": right[case]["suffix"]}
        key[case] = dict(zip(("A", "B"), versions))
        rows.append({"case": case, "split": left[case]["split"], "draft": left[case]["draft"],
                     "background": left[case]["messages"],
                     "A": choices[versions[0]], "B": choices[versions[1]],
                     "scores": {v: {k: None for k in ("accuracy", "fluency", "context", "usefulness")}
                                for v in ("A", "B")}, "preferred": None, "reason": ""})
    return {"reviewer": "", "rubric": "Score each dimension 0 (wrong/unusable), 1 (usable with edits), or 2 (good as-is). Accuracy includes scope and unsupported claims; fluency includes the draft/suffix join; context includes relevant facts and latest constraints; usefulness includes a useful next sentence. Empty output for a nonempty draft scores 0. Preferred: A, B, tie, or neither.",
            "cases": rows}, key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory")
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    report = compare(before, after)
    sheet, key = blind_sheet(before, after)
    args.output.mkdir(parents=True)
    (args.output / "comparison.md").write_text(report, encoding="utf-8")
    (args.output / "review.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "review-key.json").write_text(json.dumps({"mapping": key, "inputs": {
        str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.before, args.after)}}, indent=2), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
