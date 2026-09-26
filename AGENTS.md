# Codex setup instructions

This repository is a Windows desktop companion. When a user asks you to install or configure it, perform the setup instead of only describing it.

1. Work from the repository root. Check that the machine is Windows 10/11 x64 and has Codex Desktop or the Codex VS Code extension. Do not commit or upload files from `.local/`, `.venv/`, `dist/`, logs, user sessions, or local configuration.
2. Tell the user the first setup downloads roughly 1.5 GB of Ollama and model weights whose size depends on the chosen model (about 2.5 GB for the default). Their request to install this repository authorizes running the installer. Do not start the download for a documentation or review request.
3. Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Start`. If the user chose a model, add `-OllamaModel MODEL:TAG`. If the user supplied an HTTP proxy, add `-ProxyUrl 'http://127.0.0.1:PORT'`. The script provisions uv and Python 3.12 when absent. `-EnvironmentOnly` prepares the Python environment for development or CI. Use `-SkipModel` only when the user explicitly wants to defer the model download.
4. Check the exit code. Verify `.venv\Scripts\python.exe` runs, `http://127.0.0.1:11434/api/version` responds, the selected model appears in `/api/tags`, and the app process is running. Environment-only development setup intentionally skips the two Ollama checks.
5. Run `.\.venv\Scripts\python.exe -m pytest`. For a packaged build, rerun setup with `-Build`, then run `.\.venv\Scripts\python.exe scripts\check_package.py`.
6. Report which checks actually passed. If downloading or starting a dependency fails, report the exact command and error. Never claim that completion or Tab insertion was verified for a Codex host unless you exercised it in that host's conversation.

The app reads Codex local task logs. Treat them as private. Do not include their contents in tests, issues, commits, or diagnostics shared outside this computer.

## Development and version management

- Read `docs/DEVELOPING.md` and `docs/RELEASING.md` before changing the development or release workflow.
- Use `codex/<topic>` branches for a coherent feature or fix, starting from current `main`. Reuse a branch when continuing its existing work. If a change depends on an unmerged PR, identify that dependency explicitly.
- Make focused commits after meaningful, verified changes. Use English Conventional Commit subjects: `fix:`, `feat:`, `docs:`, `test:`, `refactor:`, or `chore:`. Mark incompatible changes with `!`. Use the same format for PR titles, since squash merges preserve that title for version selection.
- Add a short user-facing entry to `CHANGELOG.md` under `Unreleased` when behavior changes. Do not change the app version on ordinary commits.
- Run the relevant regression tests and `python scripts/version.py check`; run the full Python suite before submitting code. For UI/input changes, also run isolated Windows desktop acceptance. State whether the real host/model was exercised.
- Commit only intended source, tests, docs, and workflow files. Preserve unrelated local edits. Push and open/update a draft PR when authorized; report the commit and actual checks. Repository access alone is not permission to merge, publish, or change visibility.
- Prepare versions through the `Prepare release` GitHub Action. It updates `pyproject.toml`, `__version__`, `uv.lock`, the changelog, and a release plan together. Do not bypass its PR by committing a version bump directly to `main`.
- `auto` increments the current Beta number. After a stable release it uses Conventional Commit subjects: breaking changes bump major, features bump minor, otherwise patch. Use `stable` explicitly to graduate a Beta. Review the proposed version before merging.
- Release PRs are reviewed and merged by the user or with explicit authorization. A merged release plan creates a tag and a draft Release after checks. Publishing is a separate explicit decision, after desktop/model acceptance of the exact package.
- Never move a version tag, overwrite release assets, force-push shared work, or delete someone else's branch to repair a release. Prefer a new version. Use revert commits and a previous verified installer for rollback; preserve user settings and model files.
