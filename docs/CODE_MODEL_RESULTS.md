# First code-model trial

This is the historical trial record. The public Beta keeps only the three Qwen3 choices in its recommended list; the code adapters can still be tried by entering a tag manually. See [the current experiment instructions](CODE_MODELS.md) and [the later prompt regression check](BENCHMARK_RESULTS.md#public-beta-follow-up).

Measured locally on 2026-09-26 with an RTX 4090 (24 GB), Ollama 0.34.4 and the 40-case `completion-short-v2` suite. Each model ran once. One case has an empty draft, so latency and nonempty-output statistics cover 39 requests. The code models use native prefix continuation; the existing models use the chat adapter. This compares each model **with its adapter**, not weights in isolation.

| Model | Rule passes | Nonempty outputs | Median ms | p95 ms | Worst ms |
|---|---:|---:|---:|---:|---:|
| Qwen3 1.7B | 37/40 | 38/39 | 117.1 | 229.9 | 232.3 |
| Qwen3 4B Instruct (default) | 38/40 | 38/39 | 166.6 | 237.7 | 306.9 |
| Qwen3 8B | 39/40 | 39/39 | 213.8 | 251.0 | 268.2 |
| Qwen2.5-Coder 1.5B Base | 32/40 | 33/39 | 51.7 | 97.4 | 3489.4 |
| StarCoder2 3B | 31/40 | 30/39 | 65.8 | 88.3 | 111.5 |
| DeepSeek-Coder 1.3B Base | 29/40 | 30/39 | 62.5 | 2704.3 | 3564.4 |

These are warm backend timings, including repairs and empty results, but excluding initial model loading and desktop display. They are exploratory measurements on a working desktop, not an isolated performance study; downloading and packaging overlapped part of the Qwen Coder run. The multi-second outliers are retained and their cause has not been established. The suite was already used during earlier prompt development, so it is a regression comparison, not a newly held-out evaluation.

The small code models had lower typical latency, but more empty suggestions and several plainly bad continuations. This trial does not justify replacing the default. The larger code-model presets are available for a later trial; their weights were not downloaded or evaluated in this run.

## Examples worth reading

The following are author spot-checks of actual outputs, not an independent or blinded user study. Rule checks miss some of these problems, which is why their pass rate must not be called accuracy.

- Draft `这个页面的加载速度能不能再`: Qwen Coder and StarCoder2 both returned `快一些？`; DeepSeek returned `快一点？`. These are useful short joins.
- Draft `Please check the authentica`: all three code models returned `ion settings.`, producing the misspelled `authenticaion`. The default returned `tion settings are correct.`.
- Draft `The toolbar should remain visib`: Qwen Coder returned `l`; StarCoder2 and DeepSeek returned `ile.`. The default returned `le when the window is resized.`.
- Draft `把报错信息写清楚。`: all three code models returned no suggestion after both attempts. The default returned `请包含错误代码、发生位置和可能的触发条件。`.
- Draft `先检查缓存文件 cache.`: DeepSeek returned `json 中是否有数据，如果有，请先删除。`, adding a deletion request that the draft did not establish. The default guessed `json 中最新的版本和修改时间。`; that filename extension is also only a guess.

The adapters keep ordinary guesses available for the user to accept or reject. They do not fix spelling after generation or silently replace bad output with a different model.

## Verification and limits

- All 294 Python regression tests passed. Version consistency and packaged dependency checks passed.
- The candidate package passed the isolated desktop input/Tab suite and the normal startup/settings suite. The installed executable was verified against that candidate, its settings window opened after restart, and the existing configuration was preserved.
- The packaged default model exercised 11 synthetic desktop drafts and their insertion path before a separate grounding assertion failed. Grounding passed 12/13 cases. The same failing draft and output were reproduced with the previous commit `4f428fa` and the adapter commit `e56eba8`: `导出的报告缺没缺内容看不出来，改一下` produced `，先确认报告中哪些部分是必须保留的，再调整结构。`. This existing quality issue was not hidden by changing the check.
- The first two sandbox desktop launches could not acquire focus. The interactive-desktop run resolved that environment limitation. Failed receipts are retained locally.
- Formal release acceptance is **incomplete**: its validator requires the full repeated desktop and live-model gates. This is a local experimental build, not a release-ready claim. No real Codex-host typing or physical IME validation was performed in this trial.

## Reproduction records

Adapter source: `e56eba8b6351796d907ffade220f0c9d1cf56ab0`.

Dataset SHA256: `36ba2b45c6c44730042ca36b4826659eef4013d7d91a084eacb2ff9b6724c534`.

Harness SHA256: `c0d55f99d574ffdb5cf232b530b858d08a87a2393d79f694158142166acfd043`.

| Ollama tag | Exact digest |
|---|---|
| `qwen3:1.7b` | `8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7` |
| `qwen3:4b-instruct` | `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0` |
| `qwen3:8b` | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| `qwen2.5-coder:1.5b-base` | `02e0f2817a890a6de385d534465c04c5d0980abddc83615c09e79cee2c094446` |
| `starcoder2:3b` | `9f4ae0aff61ee24fe4b7d9714c9382b5172551fa8e95aa064452ec2e62610835` |
| `deepseek-coder:1.3b-base` | `3b417b78692522469548deacfe941aedac9cbd72a76705d9f069c90444f3a679` |

Qwen variants use Q4_K_M in this run; StarCoder2 and DeepSeek use Q4_0. Raw reports, paired review sheets, all case outputs and desktop receipts remain in `.local/code-model-trial/`. They contain synthetic inputs only and are not part of the source distribution. See [model setup and benchmark commands](CODE_MODELS.md).
