# Sentence prediction and offline Codex review

Measured on 2026-09-26 against released `v0.1.0b4` (`d7338cb`). The candidate predicts the rest of the user's sentence or their next short thought. The previous prompt explicitly asked for more requirements and verification requests.

**Keep this as a development candidate.** It better matches the intended task in this review, but still makes factual guesses, breaks some English words and loses one multi-Tab suggestion. The installed b4 app has not been replaced. This is not a release-quality or real-user acceptance claim.

## What changed

- One prompt handles unfinished words, partial sentences and next-sentence prediction. Short guesses remain available for vague input; an explicitly started plan can still be continued.
- The decoder accepts an exact full-draft echo as well as the short anchor, then keeps only new text. Rewritten prefixes remain invalid. Normal requests and the existing single bounded repair are unchanged.
- Offline Codex review judges meaning. Automatic checks cover formatting, copying and emitted text. Keyword, punctuation and expected-prefix matches are optional diagnostic hints only; they do not score meaning or hide live suggestions.

The scoring correction was necessary: a forbidden word can occur in a valid negation, and `simpl` can become either `simple` or `simpler`. Both versions were rerun with the corrected harness. Old mechanical pass rates are not comparable with the table below.

## Iteration record

Five semantic-review rounds followed the initial sentence-prediction candidate. Every development draft was inspected. Rounds 1 and 2 also have local per-case scores; rounds 3 and 5 were rejected qualitative trials. The final selected round received a fresh complete paired review.

| Round | Observed result | Decision |
|---|---|---|
| 1 | Less automatic task expansion, but invented past actions, an extra English word space and two missing suggestions | Revise |
| 2 | Better word joining in one case, but four missing suggestions | Reject |
| 3 | Shorter instructions improved some user-voice cases but broke partial words and overclaimed success | Reject |
| 4 | Restored all but one suggestion; retained short user continuations and a useful unknown-cause question | Select for final comparison |
| 5 | Simpler wording fixed one word join but again asserted an unsupported failure cause | Reject; restore round 4 |

Adding rules or shortening a prompt did not consistently improve it. Selection reflects a tradeoff, not proof that every dimension improved.

## Semantic review

Reviewer: **Codex primary agent, unblinded author self-review**. The same agent edited and reviewed the prompt; this is not independent evaluation. Each pair includes background, draft, exact suffixes, four scores and a reason. Scores range from 0 (unusable/wrong) to 2 (acceptable); 1 means editing is needed. Plausible intent and preference guesses are allowed. Unsupported assertions about evidence, completed actions and known causes are penalized. Specific requests are not inherently wrong.

The development comparison reviews the first repetition of each of 63 nonempty drafts: 39 original cases plus 24 sentence-prediction cases. Repeated timing runs do not multiply this sample count. Both historical splits were used in tuning.

| Development result | b4 | Candidate |
|---|---:|---:|
| Accuracy / role and scope | 1.714 | 1.794 |
| Fluency / cursor join | 1.905 | 1.889 |
| Context consistency | 1.873 | 1.825 |
| Usefulness | 1.476 | 1.635 |
| Preferred cases | 10 | 27 |

Another 24 cases tied; 2 were judged unsuitable in both versions. The usefulness gain accompanies slightly worse fluency and context scores. These are reviewer judgments, not calibrated accuracy percentages.

A fresh 12-case set was evaluated only after selecting round 4, with no subsequent prompt changes. Candidate preferred: **4**; b4 preferred: **1**; ties: **5**; neither: **2**. Mean usefulness was 1.583 versus 1.333; context consistency was 1.667 for both. This small sample does not establish broad superiority. Its outputs are now known and future work must treat it as regression data.

The complete synthetic judgments are committed for inspection:

- [63 development pairs](../benchmarks/reviews/sentence-prediction-development.json) and [version key](../benchmarks/reviews/sentence-prediction-development-key.json)
- [12 fresh pairs](../benchmarks/reviews/sentence-prediction-holdout.json) and [version key](../benchmarks/reviews/sentence-prediction-holdout-key.json)

## Remaining failures

| Case | Draft / relevant input | Candidate suffix | Problem |
|---|---|---|---|
| s04 | `Please check the authentica` | ` tion settings.` | Space inside the unfinished word |
| p14 | `This wording feels a bit repetit` | `itive, maybe we can rephrase it.` | Duplicates letters; b4 also fails this word in the matched run |
| s08 | `Can you check why the preview is blank?` | ` I just refreshed the page.` | Invents a completed action |
| u08 | 为什么按返回会丢掉筛选条件？ | 我试了几次都这样。 | Invents repeated attempts |
| p09 | 这个说法我不太认同，因为 | 数据支持的结论不同。 | Invents evidence |
| p11 | 这部电影我最喜欢的地方是 | 开头的那场雨中对话。 | Invents a specific scene in an unnamed movie |
| u19 | Two completed requests about drag order and persistence | Empty in all three runs | New missing-suggestion regression |
| h06 | 上次报错是由于 | 配置文件路径写错了。 | Fresh case: unsupported cause |
| h07 | 这本书我还没读完， | 打算这个周末开始。 | Fresh case: contradicts having already started |

Candidate round 4 recovers u16, which was empty in b4, so the overall display rate is unchanged even though the failing case changed. The full paired review also records weaker, generic and redundant suggestions; the table above is not the entire quality assessment.

## Mechanical checks and latency

Same installed `qwen3:4b-instruct` Q4_K_M weights, Ollama 0.34.4, zero temperature, harness and datasets. Each development suite ran three shuffled repetitions per version. The candidate includes both prompt and decoder changes, so this does not isolate their effects. Some outputs still varied between repetitions; all repeats remain in the local reports.

Timings cover warm backend requests, including retries and missing results. They exclude UIA, Qt and screen rendering and are not end-to-end user-perceived latency.

| Metric | b4 | Candidate |
|---|---:|---:|
| Original suite: mechanical checks | 117/120 | 117/120 |
| Original suite: displayed on nonempty drafts | 114/117 | 114/117 |
| Original suite: p50 / p95 | 172.2 / 221.1 ms | 153.8 / 333.2 ms |
| Original suite: retry rate | 2.6% | 5.1% |
| Sentence suite: mechanical checks | 72/72 | 72/72 |
| Sentence suite: p50 / p95 | 161.0 / 184.0 ms | 145.7 / 174.8 ms |
| Fresh set: mechanical checks, one run | 12/12 | 12/12 |

The median improves, while the original suite's tail gets worse. Every fresh output passes mechanical checks despite two clear semantic failures. No raw punctuation-only response occurred in these runs; injected-response tests cover that behavior.

## Reproduce

See the [benchmark workflow](BENCHMARK.md) for generating matched outputs and repeating the review loop. Add `--dataset benchmarks/sentence-prediction-v1.json` or `--dataset benchmarks/sentence-prediction-holdout-v2.json` to both benchmark commands. Use a fresh output path each time.

Recompute the committed development judgments without rerunning the model:

```powershell
.\.venv\Scripts\python.exe scripts/score_completion_review.py benchmarks/reviews/sentence-prediction-development.json benchmarks/reviews/sentence-prediction-development-key.json --output .local/review-development-scores.json
```

Use the two holdout files for its separate summary. Local raw runs are `matched-before[-short].json`, `matched-after[-short].json`, `matched-before-holdout.json` and `frozen-holdout.json` under `.local/sentence-prediction/`. Earlier failed candidates are preserved there and not committed.

- Model digest: `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`
- Sentence dataset SHA256: `802ef60983907b34e26aa50623231298c02d32d715403d0043b2e1ba6488bd82`
- Runner SHA256: `a2148b532df0be5d406675e0f254e799c58724e451103a47774c25e680b4219d`
- Measured prompt source SHA256: `5ad5cfa44dbbaa88bf821a9899c6783cfd66102ae6b5997892ad1ffdad60f154`
- Final prompt source SHA256: `d2291b3b0989ae6f6afbcd5cd5d9bb7be03f22826d315a5335494a767d5fa023` (only trailing blank lines removed; parsed Python content is identical)
- Selected model adapter SHA256: `b1cd05d1b6a9ac2bf53f73f7abe52c769524c7ca7753074434b0870b41bb06f5`

## Integration verification

The final source check passed 323 unit tests, download recovery and installation lifecycle fixtures, 49 isolated desktop checks, 46 live-model checks and 39 startup checks. Eleven synthetic desktop suggestions appeared and were inserted with native Tab without sending the message. The model stayed resident beyond the previous idle timeout, released on request and reloaded on the next edit. All 13 additional output-contract cases passed mechanical checks; that is not a semantic quality score. Version consistency remains at 0.1.0b4.

Receipt: `.local/sentence-prediction/final-source-acceptance-2/receipt.json`. This is a working-tree source check, not acceptance of a newly built release package. The earlier check failed a test that still counted a cursor hint as mechanical failure; that test was corrected and the full check rerun. Earlier lexical-gate failures are also preserved locally. The English README illustration was inspected as a static SVG render.

Unit tests, isolated desktop/model acceptance and semantic review are separate evidence. No check here exercises a real Codex conversation, physical keyboard input or a real IME. The installed app and published release are unchanged.
