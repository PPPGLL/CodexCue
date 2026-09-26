# Trying code models on ordinary text

CodexCue can use Qwen2.5-Coder Base, StarCoder2 and DeepSeek-Coder Base to continue your messages. They are experimental choices: coding benchmark scores do not tell us whether they write better Chinese or English drafts. The default remains `qwen3:4b-instruct`.

The [first local comparison](CODE_MODEL_RESULTS.md) includes the three small code models and the existing Qwen3 choices, with measured timings, examples and known failures.

## Switch models

Open **Settings** from the tray, choose a model, and click **Download model** if it is not installed. Save after the download finishes. The selected model loads automatically; the first suggestion can take longer while it loads. You can switch back to `qwen3:4b-instruct` in the same list. Downloading does not change the selected model until you save.

| Model | Approximate download | Intended trial |
|---|---:|---|
| `qwen2.5-coder:1.5b-base` | 0.99 GB | Small Qwen code model |
| `qwen2.5-coder:7b-base` | 4.68 GB | Larger Qwen code model |
| `starcoder2:3b` | 1.71 GB | Small StarCoder2 model |
| `starcoder2:7b` | 4.04 GB | Larger StarCoder2 model |
| `starcoder2:15b` | 9.07 GB | Largest listed option |
| `deepseek-coder:1.3b-base` | 0.78 GB | Small DeepSeek code model |
| `deepseek-coder:6.7b-base` | 3.83 GB | Larger DeepSeek code model |

Sizes are decimal GB from the Ollama manifests checked on 2026-09-26. Memory use is higher than download size and varies with the model and context. Models download only when requested; installing CodexCue does not install this whole list.

Try the same unfinished draft with each model. Check whether the suggestion joins naturally, respects the conversation, and stays in your voice. For example: `Please check the conn`, `这个按钮的位置可以`, or a complete request such as `让这段说明更容易读。`. Press Tab to accept a short continuation; press it again when the next suggestion appears. Questions should continue your request, rather than receive an assistant's answer.

## How the adapters work

Official Base tags use Ollama's `/api/generate` with `raw: true`. The prompt contains quoted background, completed example user messages, and the current draft ending exactly at the cursor. Generation produces only the new text. There is no JSON response or chat-role template for these models. Since CodexCue has no text after the cursor, the adapter uses ordinary prefix continuation, rather than inventing right-hand context for fill-in-the-middle.

The adapter stops after one substantive sentence or a document boundary. A leading separator alone does not finish a suggestion. It preserves spaces and partial-word joins, filters protocol tokens, and allows at most one repair under the existing cancellation signal and total deadline. There is no fixed typing delay and no extra request to classify the draft.

Model selection determines the protocol, not the type of request. Use the exact Base tags above; Qwen and DeepSeek's Instruct/default tags retain the chat adapter. Custom aliases are not inferred to be Base models just because their names contain `coder`.

Sources: [Qwen Base tags](https://ollama.com/library/qwen2.5-coder/tags), [StarCoder2](https://ollama.com/library/starcoder2), [DeepSeek Base tags](https://ollama.com/library/deepseek-coder/tags).

## Compare repeatable examples

The [text benchmark](BENCHMARK.md) works with either adapter. It uses synthetic drafts, never your conversation history. Models must already be installed; this command does not download weights or change the app's settings.

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --model qwen3:4b-instruct --label default --repeat 1 --output .local\model-trial\default.json
.\.venv\Scripts\python.exe scripts\benchmark_completion.py --model qwen2.5-coder:1.5b-base --label qwen-base --repeat 1 --output .local\model-trial\qwen-base.json
.\.venv\Scripts\python.exe scripts\compare_completion_benchmarks.py .local\model-trial\default.json .local\model-trial\qwen-base.json --output .local\model-trial\qwen-comparison
```

Repeat with `starcoder2:3b` and `deepseek-coder:1.3b-base`, using new output filenames. Compare on the same machine with no other generation running. Reports include exact model digests, protocol, raw responses, repairs and timings. The exported review sheet lets you judge paired suggestions with the model labels hidden. Rule checks are not a user acceptance score, and warm backend timings exclude initial loading and desktop rendering.
