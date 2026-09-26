"""Reproducible synthetic continuation benchmark against an installed local model.

No session logs, clipboard access, downloads, or desktop input. Rule checks are
diagnostics; the exported review sheet covers meaning and fluency separately.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks/completion-short-v2.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assess(case: dict, suffix: str, error: str | None = None) -> dict[str, bool]:
    """Independent, fixed observable checks; never score against prompt examples."""
    if case.get("expect_empty"):
        return {"no_error": error is None, "empty_input_silent": not suffix}
    checks = {"no_error": error is None, "nonempty": bool(suffix.strip()),
              "substantive": any(c.isalnum() for c in suffix),
              "user_voice": not bool(re.search(
                  r"^(?:\s|[。.!！])*?(?:好的[，,]|我会|我来帮|当然可以|Sure[,!]|I will|Yes[,!]|No[,!])",
                  suffix, re.I)),
              "no_protocol": not bool(re.search(r'"continuation"|<\||<｜|<fim_|<file_sep>|<think>|\banchor\b|(?:Background|User|Assistant):', suffix)),
              "length": case.get("min_chars", 1) <= len(suffix) <= case.get("max_chars", 220)}
    if case.get("no_boundary_start"):
        punctuation = r"[.;:!?。；：！？]" if case.get("allow_comma") else r"[,.;:!?，。；：！？]"
        checks["cursor_join"] = bool(suffix) and not bool(re.match(r"\s*" + punctuation, suffix))
    if "starts_with" in case:
        checks["cursor_join"] = suffix.startswith(case["starts_with"])
    if "contains_any" in case:
        checks["context_or_topic"] = all(any(w.casefold() in suffix.casefold() for w in words)
                                           for words in case["contains_any"])
    if "forbidden" in case:
        checks["constraints"] = not any(w.casefold() in suffix.casefold() for w in case["forbidden"])
    compact = lambda text: re.sub(r"[\W_]+", "", text.casefold())
    candidate = compact(suffix)
    # Entire copied fragments, not normal shared nouns or a correct fact.
    checks["no_verbatim_repeat"] = not (len(candidate) >= 14 and any(
        candidate in compact(text) for text in [case["draft"], *(m[1] for m in case.get("messages", []))]))
    return checks


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "p50": None, "p95": None}
    return {"n": len(values), "p50": round(statistics.median(ordered), 1),
            "p95": round(ordered[max(0, math.ceil(.95 * len(ordered)) - 1)], 1)}


def summarize(rows: list[dict]) -> dict:
    checks: dict[str, list[bool]] = {}
    for row in rows:
        for name, passed in row["checks"].items():
            checks.setdefault(name, []).append(passed)
    active = [r for r in rows if not r["expect_empty"]]
    first = [r["attempts"][0] for r in active if r["attempts"]]
    return {"n": len(rows), "case_pass_rate": round(sum(all(r["checks"].values()) for r in rows) / len(rows), 4),
            "checks": {name: {"passed": sum(v), "total": len(v)} for name, v in checks.items()},
            "shown_rate": round(sum(bool(r["suffix"]) for r in active) / len(active), 4) if active else None,
            "backend_ms": percentiles([r["backend_ms"] for r in active]),
            "backend_ms_shown": percentiles([r["backend_ms"] for r in active if r["suffix"]]),
            "estimated_trigger_plus_backend_ms": percentiles([r["estimated_trigger_plus_backend_ms"] for r in active]),
            "retry_rate": round(sum(r["generation_requests"] > 1 for r in active) / len(active), 4) if active else None,
            "raw_first_punctuation_only": sum(a.get("punctuation_only", False) for a in first),
            "raw_first_empty": sum(a.get("empty", False) for a in first),
            "generated_first_responses": len(first),
            "failures": dict(Counter(name for r in rows for name, passed in r["checks"].items() if not passed))}


@contextmanager
def source_tree(ref: str | None):
    if ref is None:
        yield ROOT
        return
    # Resolve first, then archive only source from a local commit. Never fetch code.
    commit = subprocess.check_output(["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", commit, "src"], cwd=ROOT)
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-source-", dir=local) as tmp:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            root = Path(tmp).resolve()
            for name in z.namelist():
                if not (root / name).resolve().is_relative_to(root):
                    raise ValueError("Archive path escapes the source snapshot")
            z.extractall(root)
        yield root


def run(args, root: Path) -> dict:
    sys.path.insert(0, str(root / "src"))
    from codex_companion import model
    from codex_companion import state
    from codex_companion.sessions import Message
    data_bytes = DATASET.read_bytes()
    cases = json.loads(data_bytes)["cases"]
    cases = [c for c in cases if args.split == "all" or c["split"] == args.split]
    quiet_ms = getattr(state, "KEYBOARD_QUIET_SECONDS", .3) * 1000
    source_files = {p.name: digest(p.read_bytes()) for p in (root / "src/codex_companion").glob("*.py")}
    backend = model.OllamaBackend(args.url, args.model)
    rows = []
    original_complete = model.complete_request
    original_native = getattr(model, "complete_native_request", None)
    attempts = []

    async def measured_complete(read, request):
        async def measured_read(messages, schema, budget):
            started = time.perf_counter()
            attempt = {"prompt_sha256": digest(json.dumps(messages, ensure_ascii=False).encode()),
                       "prompt_chars": sum(len(m["content"]) for m in messages), "token_budget": budget}
            attempts.append(attempt)
            try:
                raw = await read(messages, schema, budget)
                attempt["raw"] = raw
                try:
                    text = json.loads(raw)["continuation"]
                    anchor = model.draft_anchor(request.draft)
                    if isinstance(text, str) and text.startswith(anchor):
                        text = text[len(anchor):]
                        attempt["empty"] = not text.strip()
                        attempt["punctuation_only"] = bool(text.strip()) and not any(c.isalnum() for c in text)
                except (ValueError, KeyError, TypeError):
                    pass
                return raw
            finally:
                attempt["ms"] = round((time.perf_counter() - started) * 1000, 2)
        return await original_complete(measured_read, request)

    model.complete_request = measured_complete
    async def measured_native(read, request):
        async def measured_read(prompt, budget):
            started = time.perf_counter()
            attempt = {"prompt_sha256": digest(prompt.encode()), "prompt_chars": len(prompt),
                       "token_budget": budget, "protocol": "native-prefix"}
            attempts.append(attempt)
            try:
                raw = await read(prompt, budget)
                attempt["raw"] = raw
                attempt["empty"] = not raw.strip()
                attempt["punctuation_only"] = bool(raw.strip()) and not any(c.isalnum() for c in raw)
                return raw
            finally:
                attempt["ms"] = round((time.perf_counter() - started) * 1000, 2)
        return await original_native(measured_read, request)

    if original_native is not None:
        model.complete_native_request = measured_native
    try:
        # Provenance uses the exact installed weights, not just a mutable tag.
        tags = backend.client.get(args.url.rstrip("/") + "/api/tags").json()["models"]
        installed = next((m for m in tags if m["name"] == args.model), None)
        if not installed:
            raise ValueError("Selected model is not installed; no downloads are performed")
        version = backend.client.get(args.url.rstrip("/") + "/api/version").json()
        backend.warm()  # Excluded; this is explicitly a warm-model benchmark.
        for repeat in range(args.repeat):
            ordered = list(cases)
            random.Random(args.seed + repeat).shuffle(ordered)
            for case in ordered:
                attempts = []
                request = model.SuggestionRequest([Message(*m) for m in case.get("messages", [])], case["draft"])
                emitted = []
                began = time.perf_counter()
                error = None
                try:
                    suffix = backend.suggest(request, emitted.append, threading.Event())
                except Exception as exc:
                    suffix, error = "", type(exc).__name__ + ": " + str(exc)
                elapsed = round((time.perf_counter() - began) * 1000, 2)
                row = {"case": case["id"], "split": case["split"], "category": case["category"],
                       "repeat": repeat, "expect_empty": case.get("expect_empty", False),
                       "messages": case.get("messages", []), "draft": case["draft"], "suffix": suffix,
                       "combined": case["draft"] + suffix, "backend_ms": elapsed,
                       "estimated_trigger_plus_backend_ms": round(quiet_ms + elapsed, 2),
                       "generation_requests": len(attempts), "attempts": attempts,
                       "checks": assess(case, suffix, error), "error": error}
                row["checks"]["emitted_matches"] = emitted == ([suffix] if suffix else [])
                rows.append(row)
                print(f"{args.label} {case['id']} repeat={repeat + 1} "
                      f"{'PASS' if all(row['checks'].values()) else 'FAIL'} {elapsed:.0f}ms", flush=True)
    finally:
        model.complete_request = original_complete
        if original_native is not None:
            model.complete_native_request = original_native
        backend.close()  # Leave the shared model resident.
    return {"schema_version": 1, "label": args.label, "synthetic_only": True,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "source_ref": args.ref, "source_files_sha256": source_files,
            "model": {k: installed[k] for k in ("name", "digest", "details")}, "ollama": version,
            "completion_protocol": "native-prefix" if getattr(backend, "completion_family", None) else "chat-json",
            "dataset_sha256": digest(data_bytes), "repeat": args.repeat, "seed": args.seed,
            "harness_sha256": digest(Path(__file__).read_bytes()),
            "keyboard_quiet_ms": quiet_ms,
            "latency_scope": "warm backend; trigger-plus-backend is an estimate excluding UIA/Qt; includes failed and retried requests",
            "summary": summarize(rows),
            "by_split": {s: summarize([r for r in rows if r["split"] == s]) for s in sorted({r["split"] for r in rows})},
            "by_category": {c: summarize([r for r in rows if r["category"] == c]) for c in sorted({r["category"] for r in rows})},
            "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--ref", help="Read source from this local Git commit instead of the working tree")
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="all")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("repeat must be positive")
    if args.output.exists():
        parser.error("Use a new output filename to preserve earlier evidence")
    with source_tree(args.ref) as root:
        report = run(args, root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 1 if any(r["error"] for r in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
