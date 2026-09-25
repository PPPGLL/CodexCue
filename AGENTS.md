# Codex setup instructions

This repository is a Windows desktop companion. When a user asks you to install or configure it, perform the setup instead of only describing it.

1. Work from the repository root. Check that the machine is Windows 10/11 x64 and has Codex Desktop or the Codex VS Code extension. Do not commit or upload files from `.local/`, `.venv/`, `dist/`, logs, user sessions, or local configuration.
2. Tell the user the first setup downloads roughly 1.5 GB of Ollama and model weights whose size depends on the chosen model (about 2.5 GB for the default). Their request to install this repository authorizes running the installer. Do not start the download for a documentation or review request.
3. Run `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Start`. If the user chose a model, add `-OllamaModel MODEL:TAG`. If the user supplied an HTTP proxy, add `-ProxyUrl 'http://127.0.0.1:PORT'`. The script provisions uv and Python 3.12 when absent. Use `-AppOnly` only when the user explicitly wants a cloud backend; use `-SkipModel` only when they explicitly want to defer the model download.
4. Check the exit code. Verify `.venv\Scripts\python.exe` runs, `http://127.0.0.1:11434/api/version` responds, the selected model appears in `/api/tags`, and the app process is running. `-AppOnly` intentionally skips the two Ollama checks.
5. Run `.\.venv\Scripts\python.exe -m pytest`. For a packaged build, rerun setup with `-Build`, then run `.\.venv\Scripts\python.exe scripts\check_package.py`.
6. Report which checks actually passed. If downloading or starting a dependency fails, report the exact command and error. Never claim that completion or Tab insertion was verified for a Codex host unless you exercised it in that host's conversation.

The app reads Codex local task logs. Treat them as private. Do not include their contents in tests, issues, commits, or diagnostics shared outside this computer.
