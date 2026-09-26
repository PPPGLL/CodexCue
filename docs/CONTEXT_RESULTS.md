# 16K context validation

Measured on 2026-09-26 with Qwen3 8B Q4_K_M, Ollama 0.34.4 and an RTX 4090 24 GB. Model digest: `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`.

## What changed

The application now budgets a 16,384-token window using the installed Qwen vocabulary. It keeps fuller recent messages and the latest existing Codex compaction summary, with space for the prompt, draft, output and repair. It does not make another model call to write a summary. Unsupported tokenizer metadata falls back to a conservative UTF-8 byte bound.

Only actual user messages, final assistant replies and `compacted.payload.message` are read. Replacement history, developer instructions, tool output and reasoning records are excluded. This is useful local task context, not a guaranteed copy of Codex's entire current model input; the local log format can change.

Current drafts and newer user corrections take precedence over older summaries. Short factual reuse is allowed: the old overlap guard rejected a correct “blue” continuation, discarded the context and then guessed another color. Whole-message echoes and long copied passages remain guarded.

## Latency and cache behavior

The following synthetic requests used a 4,704-character summary plus varying dialogue lengths. “Changed history” is the first request for that input; it can reuse a prefix from the preceding request and is **not** a fully cold measurement. The edited request immediately follows it. Times include production backend processing and exclude UIA/Qt. These six requests are a diagnostic, not a latency distribution.

| Dialogue supplied | Prompt tokens reported by Ollama | Changed history | Edit to the same draft |
| --- | ---: | ---: | ---: |
| About 7,200 characters | 9,110 / 9,112 | 989 ms | 234 ms |
| About 14,400 characters | 13,430 / 13,432 | 760 ms | 252 ms |
| About 30,000 characters, trimmed to fit | 15,056 / 15,058 | 1,886 ms | 294 ms |

All six returned the latest corrected color without repair. Selection took 2–70 ms. A 256-token draft allowance keeps the history prefix stable across nearby edits. Before this change, the near-full edit moved the truncation point and took 1,401 ms; afterwards Ollama reused 15,031 of 15,058 prompt tokens. This is one matched diagnostic, not a universal speedup claim. Crossing a draft allowance boundary, receiving new history, switching tasks or losing the model cache can still be slower.

Earlier capacity trials on this machine measured roughly 7.0 GiB of model VRAM at 16K versus 5.2 GiB at 4K. Increasing the window is not free, particularly when reading new history. The window is a ceiling; small conversations do not get padded to fill it.

## Meaning and remaining limitations

The [context cases](../benchmarks/context-window-v1.json) contain 14 synthetic inputs, each run twice with the same deterministic generation settings. All 28 passed mechanical checks, with no repairs, a backend median of 258 ms and p95 of 336 ms. Repeats do not count as independent quality cases.

An unblinded Codex author review judged 12 of 14 distinct continuations usable as written. Two need improvement: the British-English example repeats an earlier sentence, and an English follow-up keeps the user's voice but drops the summary's “tomorrow” timing. The [per-case review](../benchmarks/context-window-v1.review.json) preserves every output and judgment. The first ten cases informed development; the final four were added after the prompt revision and three were judged usable. This is a small sanity check, not evidence of a general quality gain or a user acceptance rate.

Source desktop/model acceptance also exposed existing limitations outside these context cases: `configu` was followed by ` file` without finishing the word, and a greeting sounded like an assistant. Automatic display/insertion PASS does not establish semantic correctness. No keyword-based semantic gate was added to the running application.

## Verification scope

- 338 Python tests passed; version and dependency-lock checks passed.
- Source desktop acceptance passed 49 checks; installed-model desktop acceptance passed 46 checks. These exercise an isolated synthetic composer with actual Windows UIA, HTTP, popup display and insertion.
- Additional regression coverage includes compaction during incremental reads, partial large records, a bounded storage buffer, newer corrections, summary-only context, task switching, tokenizer loading and near-full cache stability.
- Tests use synthetic conversation data. Real Codex-host Tab acceptance and actual user adoption were not measured in this run. Local raw receipts remain under `.local/context-16k/`.
