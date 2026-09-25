import json

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QDialog

from codex_companion.app import SettingsDialog
from codex_companion.config import AppConfig


@pytest.mark.parametrize("contents", [b'{"enabled":', b'[]', b'\xff',
    b'{"enabled":"false"}', b'{"model_idle_seconds":-1}',
    b'{"completion_mode":"typo"}', b'{"request_timeout_seconds":NaN}'])
def test_corrupt_config_is_preserved_and_recovery_pauses_completion(tmp_path, contents):
    path = tmp_path / "config.json"
    path.write_bytes(contents)
    config = AppConfig.load(path)
    assert config.recovery_required and not config.enabled
    assert path.read_bytes() == contents
    assert config.recovery_backup.read_bytes() == contents
    config.save(path)
    assert AppConfig.load(path).enabled is False
    assert "recovery_required" not in json.loads(path.read_text())
    assert config.recovery_backup.read_bytes() == contents


def test_read_only_backup_failure_does_not_prevent_recovery(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("broken")
    monkeypatch.setattr("codex_companion.config.shutil.copy2", lambda *_: (_ for _ in ()).throw(PermissionError()))
    config = AppConfig.load(path)
    assert config.recovery_required and config.recovery_backup is None
    assert not config.enabled and path.read_text() == "broken"


def test_explicit_data_directory_is_isolated_from_legacy_and_portable(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXCUE_DATA_DIR", str(tmp_path))
    assert AppConfig.load() == AppConfig()
    AppConfig(enabled=False).save()
    assert not AppConfig.load().enabled


def test_failed_model_process_can_be_retried_and_stale_events_are_ignored(qtbot, monkeypatch):
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    launched = []
    monkeypatch.setattr(QProcess, "start", lambda process: launched.append(process))
    dialog.download_model()
    old = launched[0]
    old.errorOccurred.emit(QProcess.FailedToStart)
    assert dialog.download_process is None
    dialog.download_model()
    current = launched[1]
    old.finished.emit(1, QProcess.NormalExit)
    assert dialog.download_process is current
    current.finished.emit(1, QProcess.NormalExit)
    assert dialog.download_process is None
    dialog.download_model()
    assert len(launched) == 3


def test_setup_upgrade_preserves_backend_and_model(tmp_path, monkeypatch):
    from codex_companion.setup_config import main

    monkeypatch.setenv("CODEXCUE_DATA_DIR", str(tmp_path / "data"))
    binary = tmp_path / "ollama.exe"
    binary.write_bytes(b"fixture")
    AppConfig(backend="cloud", ollama_model="custom:1", enabled=False).save()
    monkeypatch.setenv("COMPANION_OLLAMA_EXE", str(binary))
    monkeypatch.setenv("COMPANION_OLLAMA_MODEL", "qwen3:4b-instruct")
    monkeypatch.setenv("COMPANION_SET_MODEL", "0")
    main()
    saved = AppConfig.load()
    assert saved.backend == "cloud" and saved.ollama_model == "custom:1" and not saved.enabled
    monkeypatch.setenv("COMPANION_SET_MODEL", "1")
    main()
    assert AppConfig.load().ollama_model == "qwen3:4b-instruct"


def test_failed_settings_save_does_not_change_running_config(qtbot, monkeypatch):
    config = AppConfig(ollama_model="original:1")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog.ollama_model.setCurrentText("replacement:1")
    monkeypatch.setattr(AppConfig, "save", lambda *_: (_ for _ in ()).throw(PermissionError()))
    dialog._commit_config("ollama")
    assert config.ollama_model == "original:1"
    assert dialog.result() != QDialog.Accepted
