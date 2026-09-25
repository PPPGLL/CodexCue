# CodexCue

**Autocomplete for messages you write in Codex.** This small Windows app watches the focused composer in Codex Desktop or the Codex VS Code panel, reads a short slice of the current conversation, and suggests text to append to your draft. Press **Tab** to insert a suggestion. It never sends a message for you.

The default backend is a local Ollama model. An OpenAI-compatible API is optional. This is an independent, unofficial companion; it does not use the Codex API or your Codex model quota.

![CodexCue settings with the Signal icon](design/selected-settings.png)

The app uses a quiet, light interface with a compact suggestion beside the composer. Its app and tray icon use the Signal terminal mark.

## Features

- **Conversation-aware completion.** Uses recent user messages and final assistant replies from the current Codex task, plus your unfinished draft.
- **One-keystroke acceptance.** Tab appends the visible suggestion only when the Codex composer still has focus. Otherwise Tab keeps its normal behavior.
- **Automatic task switching.** Matches the visible Codex conversation to local task logs when you click its composer. If the match is uncertain, suggestions pause until the conversation can be identified.
- **Local by default.** Runs `qwen3:4b-instruct` through Ollama on `127.0.0.1`. You can instead configure your own OpenAI-compatible service.
- **Quiet while you type.** Blank drafts produce no suggestion. A new edit invalidates old output; suggestions appear after a short pause and do not change word by word.
- **Chinese input friendly.** Suggestions and Tab acceptance pause while an IME composition or candidate window is active, then resume after the committed draft settles.
- **Display-language aware.** Settings, tray status, and notifications use Chinese on Chinese Windows display languages and English otherwise. Restart the app after changing the Windows display language.

## Quick start

**Requirements:** Windows 10/11 x64, Codex Desktop or the Codex VS Code extension, internet access for the first install, and several GB of free disk space. An RTX 4090 or similar GPU is recommended, but other hardware can be used with different latency.

Clone this repository, open PowerShell in its root directory, and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Start
```

The script installs `uv` into `.local\bin\` if needed, provisions Python 3.12 and locked project dependencies, downloads and verifies the official Ollama Windows archive, pulls the roughly 2.5 GB model, checks that Ollama is ready, and starts the companion. The Ollama archive is roughly 1.5 GB. No administrator access or preinstalled Python is required. Running this command explicitly starts those downloads; the app does not download model weights in the background.

The installation is resumable: rerun the same command if a download fails. Files created by the installer and local model weights are ignored by Git.

### Choose a local model

The setup command installs `qwen3:4b-instruct` by default. To start with a different model, pass its Ollama name:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -OllamaModel qwen3:1.7b -Start
```

| Preset | Tradeoff |
| --- | --- |
| `qwen3:1.7b` | Smaller download and lower memory use; completion quality may be less consistent. |
| `qwen3:4b-instruct` | Default; currently tested on the project's RTX 4090. |
| `qwen3:8b` | Larger model that may improve some suggestions; needs more memory and may be slower. |

Open **Settings** from the tray to switch models. **Refresh model list** shows locally installed models; **Download model** installs the selected one only when clicked. The model field also accepts any Ollama model name. Settings checks that the selected model is installed before saving. Other model families use Ollama's chat template, so their ability to return a short suffix varies. Selecting a model does not download it automatically. Download size and speed depend on the chosen model and quantization.

### Let Codex set it up

If you have Codex running on the target Windows machine, give it this repository URL and this request:

> Clone this repository, read `AGENTS.md`, run the Windows setup script with `-Start`, and verify the Python environment, Ollama service, model, and app process. If setup fails, report the failing step and exact error.

[AGENTS.md](AGENTS.md) contains the installation and verification checklist for coding agents.

## Use it

1. Start Codex Desktop or open the Codex panel in VS Code, then start CodexCue. Launching it directly opens Settings; its icon remains in the Windows system tray. Click the tray icon to open Settings, or right-click it for the status menu. If you installed without `-Start`, run `.\.venv\Scripts\codexcue.exe`.
2. Click the composer in the Codex task you want to continue and begin typing. The tray menu shows a short preview of the automatically identified conversation.
3. When a suggestion appears beside the composer, press **Tab** to append the full suggestion. Keep editing or send the message yourself when ready.

The companion reads the draft after about 300 ms without typing and checks the matched task log about every 400 ms. Newly recorded user messages and final assistant replies enter the next suggestion; an assistant reply still in progress is not used. You do not need to add punctuation to trigger a suggestion. The first request may show a model-loading state. A blank composer never requests a suggestion.

If Windows UI Automation cannot read the draft, focus the Codex composer and press **Ctrl+Alt+D** to explicitly copy the draft and request a suggestion. This fallback temporarily uses the clipboard and only guarantees restoration of plain text. Tab insertion also temporarily uses the clipboard, restoring common formats after a short delay.

## Configuration

| Goal | What to do |
| --- | --- |
| Use the default local model | Run the Quick start command. Open **Settings** from the tray to change the Ollama address or model name. |
| Install another local model | Add `-OllamaModel MODEL:TAG` to setup, or select a model in Settings and click **Download model**. |
| Use an HTTP proxy for downloads | Add `-ProxyUrl 'http://127.0.0.1:PORT'` to the setup command. Local Ollama requests bypass the proxy. |
| Use a hosted model | Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -AppOnly -Start`, then select **OpenAI-compatible API** in Settings. |
| Defer the local model download | Add `-SkipModel` to setup. Install the model before using local completion. |
| Build a Windows executable | Exit any running packaged copy, then run setup with `-Build -SkipModel`. The result is in `dist\CodexCue\`. |

For a hosted backend, enter the **API Base URL** (for example, `https://provider.example/v1`, without `/chat/completions`), model name, and API key. Remote services must use HTTPS. The API key is stored in Windows Credential Manager, not in the app configuration file. The conversation excerpt and draft will be sent to the service you choose.

## Privacy and limitations

- The local backend only connects to loopback Ollama. The repository contains no model weights, API keys, personal configuration, or conversation logs.
- The context reader takes recent real user messages and final assistant replies. It skips system, developer, tool, and assistant analysis records; recognized runtime wrappers and image path placeholders are removed. Image contents are not sent to the completion model.
- Context is capped at roughly six messages and 1,200 characters. Long turns are truncated to keep requests small.
- Diagnostic logs record state, timings, and message counts, but not draft text, conversation text, raw model output, or API keys. Open the log folder from the tray menu.
- Codex Desktop can change its Windows accessibility tree. Such changes may require an adapter update. Automatic task matching intentionally refuses ambiguous matches.
- VS Code support is experimental. On this machine, extension `openai.chatgpt-26.917.62051` exposed a focused ProseMirror composer through Windows UI Automation; the adapter read its blank state and matched the visible conversation to one local session. A hands-on check confirmed that a suggestion appeared and Tab appended it without sending. End-to-end latency has not been measured across many VS Code drafts, and a future extension update may change its accessibility tree.
- The target of a stable suggestion within one second on a warm RTX 4090 has **not** been verified with an end-to-end desktop measurement. `scripts/benchmark.py` only measures a narrower backend path.

## Troubleshooting

- **No tray icon:** Run `.\.venv\Scripts\codexcue.exe` from PowerShell to see startup errors. On first launch, complete the Settings dialog.
- **No suggestion:** Check that the tray says the app is enabled, the focused Codex composer contains nonblank text, the current conversation was identified, and the model is ready.
- **Wrong or missing task:** Click the Codex composer again to retry recognition. Use **Open diagnostic log folder** if you need to report a reproducible mismatch.
- **Download fails:** Retry with the proxy option above if your network requires one. The setup script reports the failed step.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\benchmark.py --help
```

Windows CI exercises the `uv` bootstrap, managed Python, simulated conversations and model interfaces, and a packaged build. Before a public release, repeat hands-on checks for Chinese input, conversation matching, and Tab insertion on the supported Codex Desktop and VS Code extension versions.

## License

[MIT](LICENSE). This project is not affiliated with OpenAI.
