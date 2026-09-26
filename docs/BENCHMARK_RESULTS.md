# Short completion comparison — 2026-09-26

The target changed during development: suggest one short, useful segment, then let the user press Tab again. The earlier 80–160-character expansion target is no longer a requirement. These results use `completion-short-v2.json`, not the earlier long-completion suite.

Both versions used the installed `qwen3:4b-instruct` model, warmed before timing, with three shuffled runs of 40 synthetic cases. Twenty cases were inspected during development; twenty new cases were held aside until the final short prompt was chosen. The baseline is commit `bff68ad897b2057380786d62438f334dfe8ca85e`. No alternative model was downloaded or measured.

| Measure | Before | After |
|---|---:|---:|
| Fixed keyboard wait | 300 ms | 0 ms |
| Backend median | 175.0 ms | 171.2 ms |
| Backend p95 | 403.9 ms | 200.5 ms |
| Estimated wait + backend median | 475.0 ms | 171.2 ms |
| Estimated wait + backend p95 | 703.9 ms | 200.5 ms |
| Automatic rule pass rate | 80% | 95% |
| Held-out rule pass rate | 85% | 90% |
| Nonempty output rate for nonempty drafts | 87.2% | 97.4% |
| Requests needing repair | 5.1% | 2.6% |

The estimate excludes editor recognition and popup display. Removing the fixed wait accounts for most of the median improvement; it does not make model inference instantaneous. Empty and repaired requests are included in backend timing. Repeats measure timing stability, not 120 independent writing examples.

## Reading quality

A Codex author self-review covered all 39 nonempty draft pairs. It preferred the new output in 16 cases, the old output in 3, tied 19 and rejected both in 1. On a 0–2 scale, average accuracy/fluency/context/usefulness changed from 1.41/1.718/1.41/1.256 to 1.744/1.923/1.795/1.718. The author had seen development outputs; this is not an independent blind review or measured user acceptance.

The rule score is not semantic accuracy. Remaining examples include:

- A quoted English instruction caused an empty result after both attempts for `Please check whether retrying`. The baseline produced a usable, if vague, continuation. This is a real regression.
- Both prompts gave an ambiguous fixed-position requirement for a button while its window moves. The intended coordinate frame was not resolved.
- The new output sometimes repeats a completed question or guesses a browser/device requirement without support. Those cases received lower review scores.
- The comma check rejected both outputs for `这次占用的内存比上次多`. That fragment can also be a complete clause, so the rule cannot decide the intended boundary reliably. The raw failing score was retained.
- Neither run happened to emit punctuation alone. The punctuation guard and bounded repair are verified with injected responses, not claimed as reproduced in this live sample.

The frozen prompt was not retuned on these held-out outputs. They should become regression cases if used for future tuning, with a fresh held-out set added for the next comparison.

## Reproduce and inspect

Follow [BENCHMARK.md](BENCHMARK.md). The runner captures prompt hashes, source-file hashes, raw outputs, retries and exact installed model identity. Local raw files are under `.local/completion-benchmark/`: `short-baseline-final.json`, `short-candidate-final.json`, and `short-comparison/` (including the named review and scores). They are excluded from Git.

- Dataset SHA256: `36ba2b45c6c44730042ca36b4826659eef4013d7d91a084eacb2ff9b6724c534`.
- Model digest: `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`.

Desktop acceptance separately checks real UI Automation, keyboard hooks, insertion, consecutive Tab presses, clipboard preservation, settings and startup in a synthetic host. It does not establish actual Codex-host or physical IME compatibility.
