# Measuring completion quality

Run a fixed set before and after changing the prompt. Keep the raw outputs: a green rule check can still contain an awkward sentence or an invented fact.

The current [dataset](../benchmarks/completion-short-v2.json) contains 40 synthetic cases: 20 inspected development cases and 20 fresh held-out cases. It covers unfinished words and clauses, complete statements and questions, rough requests, analysis-only constraints, paired background facts, topic changes, quoted instructions, vague openers, incremental Tab continuation and empty drafts. The target is one useful short segment per Tab, with no minimum expansion length. The previous [60-case suite](../benchmarks/completion-v1.json) is retained as the record of an earlier, longer-completion target. Scores across the two suites are not directly comparable. Neither suite reads real conversations or the clipboard. Categories belong to the evaluator; the app does not use them to route requests.

Use development cases to choose a prompt. Freeze that prompt before reading held-out outputs. Once held-out failures influence a change, those cases become regression/development cases for that change; add a fresh held-out set before claiming another unbiased comparison. Prompt examples and test drafts must stay separate.

## Run and compare

Use the installed local Ollama model. This does not download or switch the app's configured model.

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --ref v0.1.0b3 --label before --repeat 3 --output .local\bench\before.json
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --split dev --label trial --repeat 1 --output .local\bench\trial.json
# After choosing a prompt, evaluate the entire fixed set:
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --label after --repeat 3 --output .local\bench\after.json
.\.venv\Scripts\python.exe scripts\compare_completion_benchmarks.py .local\bench\before.json .local\bench\after.json --output .local\bench\comparison
```

`--ref` reads source from a local Git commit without switching branches. Run both versions with the same dataset, harness, model weights and generation settings on an otherwise idle GPU. Reports include model digest, Ollama version, source-file hashes, dataset/harness hashes, prompt hashes, raw responses, retries and per-case timings. Existing reports cannot be overwritten. Use a new filename for each experiment. A completed measurement exits successfully even when quality rules fail; inspect the scores. Transport/model errors exit with failure.

The runner warms the model first and leaves it resident. `backend_ms` includes the whole production request, including a repair when needed. It reports p50/p95 for all nonempty drafts, with a separate view for displayed outputs so suppressing suggestions cannot silently make latency look better. Trigger-plus-backend time is an **estimate**, adding the configured keyboard wait; it excludes UI Automation, Qt and popup rendering. The desktop acceptance test measures a different path. Repeated deterministic runs help assess timing stability; they do not multiply the number of independent quality cases.

## Read the results

Automatic checks cover visible content, word/phrase joins, verbatim repetition, protocol leakage, selected context facts, explicit constraints and broad length expectations. They also report raw punctuation-only/empty responses before repair. Length is a useful regression signal, not a definition of good writing. The punctuation-only guard is also covered with deliberately injected model responses, because a live sample may never happen to emit that failure.

These checks have known limits. For example, “Windows only, not macOS” can trigger a forbidden-word check despite being correct. A made-up cause followed by “can you confirm?” can pass a question-word check. Do not call the overall rule pass rate “accuracy.” Do not weaken a check after seeing a bad result to make a release pass. A scoring correction needs a documented reason and both versions must be rescored with the same versioned suite.

The comparison also exports `review.json` and a separate `review-key.json`. Review each draft **together with** its suffix and background, without consulting the key. Score accuracy, fluency, context and usefulness from 0 to 2: 0 is unusable/wrong, 1 needs edits, 2 can be accepted as written. Record A/B/tie/neither and a reason. Name the reviewer; an author self-review is not an independent user study. Keep empty suggestions in the comparison and mark them unusable for nonempty drafts. Summarize average dimension scores and preference counts only after the review is complete.

```powershell
.\.venv\Scripts\python.exe scripts\score_completion_review.py .local\bench\comparison\review.json .local\bench\comparison\review-key.json --output .local\bench\comparison\review-scores.json
```

The scorer refuses incomplete, duplicate or missing cases. Keep the filled review with the scores so each judgment can be checked.

For a prompt change, inspect new cursor-join, assistant-answer or unsupported-fact failures in reviewed cases, preserve useful short continuations, and check that rough requests gain one concrete requirement. Check p95 as well as the median. If quality and latency trade off, report both rather than hiding them in one weighted score. Real user acceptance and subsequent edits remain a separate product measure; this benchmark does not collect them.

See [public tools and model candidates](COMPLETION_RESEARCH.md) for the source material behind this approach. Code-completion leaderboards do not establish Chinese message-completion quality.

The [short-completion comparison](BENCHMARK_RESULTS.md) records measured improvements and remaining failures from the first run of the current suite.

For comparisons across model families, see [trying code models on ordinary text](CODE_MODELS.md). The runner also records raw native-prefix responses and repairs. Compare the complete model-and-adapter combination; these runs do not isolate model weights from prompt formatting.
