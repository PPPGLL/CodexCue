# Third-party software in CodexCue

CodexCue itself is MIT licensed; see `LICENSE`. Dependency licenses are separate.
`licenses/inventory.json` lists the exact versions and license files collected
from the build environment. Python's license and the PyInstaller bootloader's
license and exception are included. Ollama and model weights are not in this ZIP.

## Qt, PySide6 and Shiboken

This distribution uses the open-source LGPLv3 licensing option for the Qt modules,
PySide6 and Shiboken used by the application. The Qt commercial license text may
also appear in upstream wheel metadata; it is not the license chosen for this app.
Qt copyright, third-party attribution and license notices are retained in the
`licenses/qt-qtbase` and `licenses/pyside-pyside-setup` directories. Not every
upstream component listed there is linked into this application.

The same release provides the exact, unmodified corresponding Qt Base and
PySide/Shiboken source archives as separate `*-source.tar.gz` assets; their
upstream commit IDs and SHA256 values appear in `licenses/inventory.json` and
`SHA256SUMS.txt`. The application's own source is available in the release source
ZIP and the repository. Keep these source assets with any binary redistribution.
Upstream build instructions are included in those source archives.

Qt and Python extension libraries remain as separate files under `_internal`.
You may replace compatible LGPL libraries with modified builds for your own use
and debugging; CodexCue imposes no restriction on reverse engineering for that
purpose. Keep library ABI, architecture and Python version compatible. Rebuild
the app with `uv sync --locked --extra build` and `python -m PyInstaller
codexcue.spec` using the provided sources and lockfile when needed. Do not run the
installer's integrity check on a locally modified bundle; use that build directly.

The release checksum verifies distribution integrity. It does not limit your
rights under the applicable open-source licenses.
