import logging

import pytest

from codex_companion import diagnostics


def test_log_records_state_without_accepting_conversation_text(tmp_path, monkeypatch):
    logger = logging.getLogger("codex_companion_test_diagnostics")
    logger.handlers.clear()
    monkeypatch.setattr(diagnostics, "_LOGGER", logger)
    path = diagnostics.setup_logging(tmp_path / "logs" / "companion.log")
    try:
        diagnostics.log_event("draft_observed", draft_len=3, hwnd=123,
                              bounds=(1, 2, 3, 4), generation=7)
        diagnostics.log_event("settings_presented", hwnd=456, visible=True,
                              bounds=(10, 20, 30, 40), foreground=456)
        with pytest.raises(ValueError):
            diagnostics.log_event("draft_observed", draft="秘密草稿")
        with pytest.raises(ValueError):
            diagnostics.log_event("request_failed", reason="contains private text")
        diagnostics.shutdown_logging()  # Drain the background writer before reading.
        content = path.read_text(encoding="utf-8")
        assert "draft_len=3" in content
        assert "settings_presented hwnd=456 visible=True" in content
        assert "秘密草稿" not in content
    finally:
        diagnostics.shutdown_logging()


def test_unwritable_user_log_uses_program_directory(tmp_path, monkeypatch):
    logger = logging.getLogger("codex_companion_test_log_fallback")
    logger.handlers.clear()
    monkeypatch.setattr(diagnostics, "_LOGGER", logger)
    monkeypatch.setattr(diagnostics, "default_config_path",
                        lambda: tmp_path / "locked" / "config.json")
    monkeypatch.setattr(diagnostics.sys, "executable", str(tmp_path / "program" / "app.exe"))
    original = diagnostics.RotatingFileHandler

    def handler(path, *args, **kwargs):
        if "locked" in str(path):
            raise PermissionError("simulated denied directory")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(diagnostics, "RotatingFileHandler", handler)
    try:
        path = diagnostics.setup_logging()
        assert path == tmp_path / "program" / "logs" / "companion.log"
        diagnostics.log_event("app_started")
        diagnostics.shutdown_logging()
        assert path.read_text(encoding="utf-8").strip().endswith("app_started")
    finally:
        diagnostics.shutdown_logging()
