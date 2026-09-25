"""Unit tests must never read or overwrite the developer's runtime data."""
import pytest


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path, monkeypatch):
    from codex_companion import config

    monkeypatch.delenv("CODEXCUE_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "synthetic-codex-home"))
    monkeypatch.setattr(config, "portable_config_path", lambda: tmp_path / "portable" / "config.json")
