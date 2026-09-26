"""Aggregate a completed named review; never fill scores automatically."""
import argparse
import json
from pathlib import Path
from statistics import mean


def score(review: dict, key: dict) -> dict:
    if not review.get("reviewer", "").strip():
        raise ValueError("Name the reviewer and whether this is an author self-review")
    cases = review["cases"]
    ids = [r["case"] for r in cases]
    if not cases or len(set(ids)) != len(ids) or set(ids) != set(key["mapping"]):
        raise ValueError("Review must contain each paired case exactly once")
    dimensions = ("accuracy", "fluency", "context", "usefulness")
    results = {v: {k: [] for k in dimensions} for v in ("before", "after")}
    preference = {k: 0 for k in ("before", "after", "tie", "neither")}
    for row in cases:
        mapping = key["mapping"][row["case"]]
        if set(mapping) != {"A", "B"} or set(mapping.values()) != {"before", "after"}:
            raise ValueError(f"Invalid version mapping: {row['case']}")
        for choice in ("A", "B"):
            for dimension in dimensions:
                value = row["scores"][choice][dimension]
                if type(value) is not int or value not in (0, 1, 2):
                    raise ValueError(f"Missing/invalid score: {row['case']} {choice} {dimension}")
                results[mapping[choice]][dimension].append(value)
        winner = row["preferred"]
        if winner not in ("A", "B", "tie", "neither") or not row.get("reason", "").strip():
            raise ValueError(f"Missing preference/reason: {row['case']}")
        preference[mapping.get(winner, winner)] += 1
    return {"reviewer": review["reviewer"], "cases": len(cases),
            "mean_scores_out_of_2": {v: {k: round(mean(values), 3) for k, values in d.items()}
                                    for v, d in results.items()}, "preferences": preference,
            "scope": "Named reviewer judgments, not measured real-user acceptance"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("key", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output file")
    result = score(json.loads(args.review.read_text(encoding="utf-8")),
                   json.loads(args.key.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
