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
