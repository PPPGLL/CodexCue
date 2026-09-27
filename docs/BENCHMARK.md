# Measuring completion quality

Run a fixed set before and after changing the prompt. Keep the raw outputs: a green rule check can still contain an awkward sentence or an invented fact.

The default [regression dataset](../benchmarks/completion-short-v2.json) contains 40 synthetic cases, originally split into 20 development and 20 held-out cases. Both halves have now been inspected during prompt work. It covers unfinished words and clauses, complete statements and questions, rough requests, analysis-only constraints, paired background facts, topic changes, quoted instructions, vague openers, incremental Tab continuation and empty drafts.

The [sentence-prediction suite](../benchmarks/sentence-prediction-v1.json) adds 24 synthetic cases for the current goal: finish the user's sentence or predict their next short thought, without automatically expanding it into work. It includes ordinary conversation, closing remarks and explicitly requested plans. Cases and review expectations were frozen before model runs; subsequent tuning used failures from both original splits, so the final comparison is a development/regression result, **not an untouched holdout score**. Use `--dataset benchmarks/sentence-prediction-v1.json` on both runs to select it.

The previous [60-case suite](../benchmarks/completion-v1.json) records an earlier, longer-completion target. Scores across different suites are not directly comparable. No suite reads real conversations or the clipboard. Categories belong to the evaluator; the app does not use them to route requests.

Use development cases to choose a prompt. Freeze that prompt before reading held-out outputs. Once held-out failures influence a change, those cases become regression/development cases for that change; add a fresh held-out set before claiming another unbiased comparison. Prompt examples and test drafts must stay separate.

## Run and compare

Use the installed local Ollama model. This does not download or switch the app's configured model.

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --ref v0.1.0b4 --label before --repeat 3 --output .local\bench\before.json
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --split dev --label trial --repeat 1 --output .local\bench\trial.json
# After choosing a prompt, evaluate the entire fixed set:
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --label after --repeat 3 --output .local\bench\after.json
.\.venv\Scripts\python.exe scripts\compare_completion_benchmarks.py .local\bench\before.json .local\bench\after.json --output .local\bench\comparison
```

`--ref` reads source from a local Git commit without switching branches. Run both versions with the same dataset, harness, model weights and generation settings on an otherwise idle GPU. Reports include model digest, Ollama version, source-file hashes, dataset/harness hashes, prompt hashes, raw responses, retries and per-case timings. Existing reports cannot be overwritten. Use a new filename for each experiment. A completed measurement exits successfully even when output checks fail; inspect the report. Transport/model errors exit with failure.

The runner warms the model first and leaves it resident. `backend_ms` includes the whole production request, including a repair when needed. It reports p50/p95 for all nonempty drafts, with a separate view for displayed outputs so suppressing suggestions cannot silently make latency look better. Trigger-plus-backend time is an **estimate**, adding the configured keyboard wait; it excludes UI Automation, Qt and popup rendering. The desktop acceptance test measures a different path. Repeated deterministic runs help assess timing stability; they do not multiply the number of independent quality cases.

## Read the results

Automatic pass/fail checks cover nonempty substantive text, protocol leakage, verbatim copying, length and agreement between returned and emitted text. They also report raw punctuation-only/empty responses before repair. These are mechanical checks: every row starts with `semantic_review: NOT_REVIEWED`. Desktop acceptance likewise separates successful display/Tab insertion from meaning. The punctuation-only guard is also covered with injected model responses, because a live sample may never emit that failure.

Topic words, apparent assistant openings, expected prefixes and punctuation at the cursor appear only under `diagnostic_signals`, outside pass/fail. They are optional search hints, not judgments: “not macOS” can be correct, “I will” can be the user's intention, and `simpl` can become either `simple` or `simpler`. Review the whole combined text. Do not call the mechanical pass rate “accuracy.” Scoring corrections need a documented reason and matched reruns or explicit rescoring of both versions.

The comparison also exports `review.json` and a separate `review-key.json`. Codex can review each draft **together with** its suffix and background offline; no reviewer or remote API is added to the app. Prefer not to consult the key. If the reviewer already knows the versions, explicitly label the review unblinded. Score accuracy, fluency, context and usefulness from 0 to 2: 0 is unusable/wrong, 1 needs edits, 2 can be accepted as written. Record A/B/tie/neither and a reason. Name the reviewer; an author self-review is not an independent user study. Keep empty suggestions and mark them unusable for nonempty drafts. Summarize scores only after every pair has a judgment.

```powershell
.\.venv\Scripts\python.exe scripts\score_completion_review.py .local\bench\comparison\review.json .local\bench\comparison\review-key.json --output .local\bench\comparison\review-scores.json
```

The scorer refuses incomplete, duplicate or missing cases. Keep the filled review with the scores so each judgment can be checked.

## Iterate with Codex

1. Write synthetic cases and review expectations before running the model. Cover partial words, unfinished thoughts, complete sentences, questions, vague beginnings, context changes and explicit constraints. Reserve a fresh set for the final check.
2. Generate the baseline and candidate on the same cases. Ask Codex to score every pair using the rubric above, including failures. Reasonable guesses about intent are allowed; invented evidence, completed actions and known causes require support. A natural specific request is not automatically an error.
3. Group the observed failures, change one prompt idea, and rerun the fixed cases. Preserve raw outputs, source hashes and review reasons. Compare missing suggestions and p95 latency too. Revert unsuccessful candidates; later iterations need not improve.
4. Freeze the selected prompt, then generate and review the fresh cases. If those failures inform another edit, they become development cases; add another untouched set before the next final check. Report remaining failures even when every mechanical check passes.
5. Run source/desktop regression separately. Review candidate-package samples before replacing the installed app. Record real-host input and actual user acceptance separately from synthetic-model review.

The [12-case holdout](../benchmarks/sentence-prediction-holdout-v2.json) was first evaluated after selecting the candidate in the [current report](SENTENCE_PREDICTION_RESULTS.md). Its outputs are now known, so future prompt work must treat it as regression data. Only curated synthetic cases and judgments belong in Git; local reports and real conversations stay private.

For a prompt change, inspect new cursor-join, assistant-answer or unsupported-fact failures. A rough request does not have to gain another requirement: judge whether the suffix sounds like the same user continuing to type. Check that success messages and closing remarks do not routinely turn into new assignments, while a user explicitly writing a plan can still finish it. Check p95 and missing suggestions as well as the median. If quality and latency trade off, report both rather than hiding them in one weighted score. Real user acceptance and subsequent edits remain a separate product measure; this benchmark does not collect them.

See [public tools and model candidates](COMPLETION_RESEARCH.md) for the source material behind this approach. Code-completion leaderboards do not establish Chinese message-completion quality.

The [short-completion comparison](BENCHMARK_RESULTS.md) records measured improvements and remaining failures from the first run of the current suite.

The [sentence-prediction comparison](SENTENCE_PREDICTION_RESULTS.md) records the new objective, paired outputs and remaining regressions.

For comparisons across model families, see [trying code models on ordinary text](CODE_MODELS.md). The runner also records raw native-prefix responses and repairs. Compare the complete model-and-adapter combination; these runs do not isolate model weights from prompt formatting.
