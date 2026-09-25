# CodexCue for Windows (Beta)

Supports Windows 10/11 x64. Codex Desktop is the primary host; the Codex VS Code
extension remains experimental. A local Ollama model or an OpenAI-compatible
service is required. This ZIP does not include Ollama or model weights.

## Install and start

1. Download the Windows ZIP and `SHA256SUMS.txt` from the same release. Compare
   `Get-FileHash .\CodexCue-*-windows-x64.zip -Algorithm SHA256` with that file.
2. Extract the ZIP. For portable use, run `CodexCue.exe` in the extracted folder.
   Keep the `_internal` directory beside it.
3. For a managed per-user install, run from the extracted `CodexCue` folder:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Start
   ```

   The default location is `%LOCALAPPDATA%\Programs\CodexCue`. No administrator
   access is needed. `-InstallRoot 'D:\Apps\CodexCue'` chooses another location.
4. In Settings choose local Ollama or your hosted backend. A hosted backend sends
   the draft and a short conversation excerpt to the selected service. Keys are
   stored in Windows Credential Manager. Local inference stays on loopback.

For an automatic local-model setup, use the source checkout's `scripts/setup.ps1
-Start`: it downloads roughly 1.5 GB of Ollama plus about 2.5 GB for the default
model. Downloads are explicit. The source README documents proxies and models.

The ZIP is currently unsigned; do not treat a checksum as a publisher signature.
Use the repository's release page as the download source. A code-signing
certificate and Windows reputation are not included in this Beta.

## Upgrade and rollback

Extract a new release outside the installed folder and run its `install.ps1`
with the same `-InstallRoot`. It verifies every release file, stages the new
version, stops only that installation's app process and replaces its files.
A failed directory swap restores the previous install. User settings and
Ollama models stay outside the managed application folder.

To downgrade, extract an older verified release and run that release's installer
against the same location. Store personal files outside the managed application
folder: maintenance refuses to remove files absent from the release manifest.
Portable users should extract into a new directory and retain the old directory
until the new version works. Settings are shared between versions.

## Uninstall and data retention

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\CodexCue\uninstall.ps1"
```

This removes only the managed application. It preserves these separately owned
items so reinstalling does not lose settings or force model downloads:

- Settings and diagnostic logs: `%LOCALAPPDATA%\CodexCue`.
- Source-checkout fallback configuration, tools and model files: `<checkout>\.local`.
- API credentials: Windows Credential Manager entries for `codexcue` (or the
  older `codex-composer-companion` service) associated with the configured URL.
- Ollama and its model library, which may also be used by other applications.

To remove retained data, close CodexCue and delete only its settings/log folder,
remove its corresponding credential entries in Windows Credential Manager, and
use `ollama rm MODEL:TAG` only for models you no longer need. Never delete shared
Ollama data just to uninstall this companion. `CODEXCUE_DATA_DIR` optionally
selects an explicit settings/log directory for isolated deployments and tests.

## Recovery and support

- Corrupt configuration: startup pauses completion and opens Settings. When the
  directory is writable, the exact original bytes remain in a
  `config.json.corrupt-*.bak` file. Save corrected settings and enable completion
  from the tray. Configuration contents are not written to diagnostic logs.
- Download error: correct the Ollama executable/service and retry. The installer
  resumes matching partial archives, but starts fresh after checksum failure or
  when the selected release changes. `-OllamaVersion vX.Y.Z` pins a source setup.
- No suggestion: check the tray's enabled/model/context status. Use `Ctrl+Alt+D`
  for the explicit clipboard fallback when UI Automation cannot read the draft.
- For reports, include the version/commit from `release.json`, Windows and Codex
  versions, backend/model, and steps to reproduce. Do not attach task logs, drafts,
  API keys, or the full configuration. Numeric diagnostics are under the tray's
  **Open diagnostic log folder** action.

Automated desktop tests use synthetic inputs. They do not certify every Codex
version, Chinese IME or GPU. Published timings from an RTX 4090 are not a latency
guarantee on other hardware. First-generation and cold-model latency may vary.
