from pathlib import Path
import json

from codex_companion.config import AppConfig
from codex_companion.setup_config import main


def test_setup_saves_runtime_paths_without_model_files(tmp_path, monkeypatch):
    import codex_companion.config as config_module

    executable = tmp_path / "ollama.exe"
    executable.write_bytes(b"mock")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(config_module, "portable_config_path",
                        lambda: tmp_path / "portable" / "companion-config.json")
    monkeypatch.setenv("COMPANION_OLLAMA_EXE", str(executable))
    monkeypatch.setenv("COMPANION_OLLAMA_MODELS", str(tmp_path / "models"))
    monkeypatch.setenv("COMPANION_OLLAMA_MODEL", "qwen3:1.7b")
    main()
    config = AppConfig.load()
    assert config.ollama_url == "http://127.0.0.1:11434"
    assert config.ollama_executable == str(executable)
    assert config.ollama_models_dir == str(tmp_path / "models")
    assert config.ollama_model == "qwen3:1.7b"


def test_config_uses_portable_fallback_when_user_folder_is_unwritable(tmp_path, monkeypatch):
    import codex_companion.config as config_module

    locked = tmp_path / "locked" / "config.json"
    portable = tmp_path / "portable" / "companion-config.json"
    monkeypatch.setattr(config_module, "default_config_path", lambda: locked)
    monkeypatch.setattr(config_module, "portable_config_path", lambda: portable)
    original_mkdir = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == locked.parent:
            raise PermissionError("simulated denied user folder")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    config = AppConfig(enabled=False)
    config.save()
    assert AppConfig.load(portable).enabled is False


def test_legacy_manual_task_settings_are_discarded(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"enabled": True, "auto_session": False,
                                "selected_session": "old-task.jsonl"}), encoding="utf-8")

    config = AppConfig.load(path)
    config.save(path)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "auto_session" not in saved
    assert "selected_session" not in saved


def test_codexcue_reads_legacy_config_then_saves_under_new_name(tmp_path, monkeypatch):
    import codex_companion.config as config_module

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(config_module, "portable_config_path",
                        lambda: tmp_path / "missing-portable.json")
    old = tmp_path / "CodexComposerCompanion" / "config.json"
    old.parent.mkdir()
    old.write_text('{"enabled": false, "ollama_model": "legacy-model"}', encoding="utf-8")

    assert config_module.existing_config_path() == old
    config = AppConfig.load()
    assert not config.enabled and config.ollama_model == "legacy-model"
    config.save()
    new = tmp_path / "CodexCue" / "config.json"
    assert new.is_file()
    assert AppConfig.load().ollama_model == "legacy-model"
