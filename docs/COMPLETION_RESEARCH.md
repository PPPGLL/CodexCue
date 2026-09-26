# What public completion tools can teach us

Checked on 2026-09-26. These are public implementation examples, not a popularity ranking or leaked proprietary prompts.

GitHub's [Copilot engineering article](https://github.blog/ai-and-ml/github-copilot/how-github-copilot-is-getting-better-at-understanding-your-code/) describes selecting relevant nearby context and filling at the cursor. Its historical A/B results measure accepted code suggestions, not the quality of Chinese messages. The article does not publish Copilot's current production prompt.

Continue's [model guide](https://github.com/continuedev/continue/blob/5522c6f44ca0ac3528b37244818fbfa39b5af470/docs/customize/model-roles/autocomplete.mdx) explains why a chat model is not interchangeable with a model trained to fill gaps. Its [templates](https://github.com/continuedev/continue/blob/5522c6f44ca0ac3528b37244818fbfa39b5af470/core/autocomplete/templating/AutocompleteTemplate.ts) publish model-specific prefix/suffix markers, stop tokens, and a separate hole-filling prompt for chat models. Tabby's [prompt builder](https://github.com/TabbyML/tabby/blob/21b29048d7bcf6b94f9f482f2d0fd05efadfd19f/crates/tabby/src/services/completion/completion_prompt.rs) bounds retrieved context and prioritizes declarations, changed files, then recently opened files.

For CodexCue, the transferable ideas are to make the cursor boundary explicit, keep relevant conversation separate from instructions, and evaluate the actual continuation. Adding more examples is not automatically better: it can bias a small model toward their topics. No classifier or extra model call is needed to select a completion style.

## Models with public formats and evaluations

| Candidate | Public completion design | Public evaluation | Fit for this app |
|---|---|---|---|
| Qwen2.5-Coder Base, especially 1.5B and 7B | [Official FIM and repository-context examples](https://github.com/QwenLM/Qwen3-Coder/blob/d5ead97fea36bca3744e1a4c49e882f6fa2f406a/README.md#3-file-level-code-completion-fill-in-the-middle) | [Official report](https://qwenlm.github.io/blog/qwen2.5-coder-family/) describes HumanEval-Infilling, CrossCodeEval, CrossCodeLongEval, RepoEval and SAFIM. Exact-match and pass@1 measure different things. | Useful small/medium baselines for an experiment; Chinese message continuation still needs our own test set. |
| StarCoder2, 3B / 7B / 15B | [Official repository](https://github.com/bigcode-project/starcoder2) explicitly targets code completion rather than instruction following; its paper and Continue's template document FIM. | [BigCode evaluation harness](https://github.com/bigcode-project/bigcode-evaluation-harness) and the linked model paper/leaderboard. | A comparison baseline, not an established upgrade for conversational writing. |
| DeepSeek-Coder Base, 1.3B / 6.7B | [Official insertion example](https://github.com/deepseek-ai/DeepSeek-Coder#2-code-insertion) uses prefix/hole/end markers. Training includes English and Chinese natural language alongside code. | [SAFIM](https://github.com/gonglinyuan/safim) publishes prompts, generation/evaluation commands and results: its documented 1.3B example gets 54.10% pass@1 on **control-flow completion**. This is not an overall score or a Chinese-writing result. | Small, older baseline with unusually reproducible completion-specific evaluation. |

Qwen2.5-Coder's 0.5B, 1.5B, 7B, 14B and 32B releases use Apache 2.0; its 3B release uses the Qwen Research license, according to the official release article. Other families' weight licenses must be checked for the exact checkpoint; repository-code licenses do not necessarily cover weights. The term “open weights” does not imply every model has the same permissions.

The Qwen repository now defaults to Qwen3-Coder. The link above pins the earlier Qwen2.5 documentation so its examples cannot silently turn into examples for another model. Current Qwen3-Coder also publishes FIM, but its 30B-A3B model has **30B total parameters**, despite activating fewer per token. It is not a 3B-memory replacement for a small desktop model.

A native Qwen2.5-Coder FIM request has this shape:

```text
<|fim_prefix|>text before the cursor<|fim_suffix|>text after it<|fim_middle|>
```

That format is a training contract for a compatible model. Pasting these tokens into the current Qwen3 chat prompt would not turn it into an FIM model. A future comparison should give each model its native template, then score its plain inserted text with the same dataset. Do not compare a Base FIM model through CodexCue's current chat/JSON protocol and call the resulting score the model's capability.

## Evidence and scope

The first three tool sources are `[CONTEXTUAL] [API-USAGE] [DEBUGGING-ONLY] [OFFICIAL]`; the model repositories and official release report have the same classification. SAFIM is a maintainer-provided reproducibility reference for code evaluation. English-language GitHub code search and direct official-source reads were used. Forum and Q&A profiles were skipped because the public implementations answered the design question. An attempted browser search timed out; Hugging Face model-card reads were unavailable from this connection, so licensing claims here are limited to the accessible official Qwen report.

These GitHub and web sources inform software design and debugging. They are not being used as paper-citation evidence or as proof of a quality or speed improvement in CodexCue. Only local measurements can establish that.
