from types import SimpleNamespace

import pytest

from codex_companion import startup


def test_normal_start_does_not_relaunch(monkeypatch):
    monkeypatch.setattr(startup, "hidden_startup", lambda: False)
    monkeypatch.setattr(startup.subprocess, "Popen", lambda *a, **k: pytest.fail("unexpected relaunch"))
    assert startup.normalize_startup() is None


@pytest.mark.parametrize("mode,expected,waited", [("--background", 0, False),
                                                   ("--self-test", 7, True),
                                                   ("--startup-test", 7, True)])
def test_hidden_launch_is_normalized_and_checks_propagate_exit(monkeypatch, mode, expected, waited):
    calls = []
    waits = []
    monkeypatch.setattr(startup, "hidden_startup", lambda: True)
    monkeypatch.setattr(startup.sys, "argv", ["app", mode, "--output", "path with spaces"])
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(startup.sys, "executable", "C:\\App Path\\CodexCue.exe")

    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(wait=lambda: waits.append(True) or 7)

    monkeypatch.setattr(startup.subprocess, "Popen", spawn)
    assert startup.normalize_startup() == expected
    args, options = calls[0]
    assert args == ["C:\\App Path\\CodexCue.exe", mode, "--output", "path with spaces"]
    assert options["startupinfo"].dwFlags == startup.subprocess.STARTF_USESHOWWINDOW
    assert options["startupinfo"].wShowWindow == 1
    assert options["creationflags"] == startup.subprocess.CREATE_NO_WINDOW
    assert bool(waits) == waited


def test_source_relaunch_uses_module_entrypoint(monkeypatch):
    monkeypatch.delattr(startup.sys, "frozen", raising=False)
    assert startup.command(["--background"]) == [startup.sys.executable, "-m", "codex_companion", "--background"]


def test_console_service_descendants_inherit_a_hidden_console(tmp_path):
    """Reproduce Ollama-style helper spawning without a model or global input."""
    import json
    import subprocess
    import sys

    fixture = tmp_path / "console_service.py"
    fixture.write_text('''import ctypes, json, subprocess, sys
from pathlib import Path
kernel = ctypes.WinDLL("kernel32")
kernel.GetConsoleWindow.restype = ctypes.c_void_p
user = ctypes.WinDLL("user32")
user.IsWindowVisible.argtypes = [ctypes.c_void_p]
hwnd = kernel.GetConsoleWindow()
result = {"console": hwnd, "visible": bool(user.IsWindowVisible(hwnd))}
depth = int(sys.argv[1])
if depth:
    subprocess.run([sys.executable, __file__, str(depth - 1)], check=True, timeout=10)
Path(__file__).with_name(str(depth) + ".json").write_text(json.dumps(result))
''', encoding="utf-8")
    with subprocess.Popen([sys.executable, str(fixture), "2"], cwd=tmp_path,
                          **startup.hidden_console_options(),
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE) as process:
        _, error = process.communicate(timeout=30)
    assert process.returncode == 0, error
    states = [json.loads((tmp_path / f"{depth}.json").read_text()) for depth in range(3)]
    assert all(state["console"] for state in states)
    assert len({state["console"] for state in states}) == 1
    assert not any(state["visible"] for state in states)
