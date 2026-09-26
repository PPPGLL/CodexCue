"""Small privacy-preserving event log for diagnosing desktop timing issues."""
from __future__ import annotations

import logging
import atexit
import queue
import re
import sys
from logging.handlers import RotatingFileHandler
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path

from .config import default_config_path

_LOGGER = logging.getLogger("codex_companion")
_LISTENER: QueueListener | None = None
_ACTIVE_PATH: Path | None = None
_SAFE_FIELDS = {
    "backend", "bounds", "draft_len", "enabled", "error_type", "foreground",
    "generation", "hwnd", "kind", "latency_ms", "reason", "revision", "session_id",
    "shown", "suggestion_len", "visible_count", "visible_chars",
    "uia_ms", "index_ms", "context_count", "context_chars", "context_roles", "visible", "raw_len",
}
_SAFE_WORD = re.compile(r"^[a-z0-9_]+$")


def log_path() -> Path:
    return _ACTIVE_PATH or default_config_path().parent / "logs" / "companion.log"


def setup_logging(path: Path | None = None) -> Path | None:
    global _LISTENER, _ACTIVE_PATH
    if _LOGGER.handlers:
        return _ACTIVE_PATH
    primary = path or default_config_path().parent / "logs" / "companion.log"
    fallback = Path(sys.executable).resolve().parent / "logs" / "companion.log"
    sink = None
    for candidate in (primary, fallback) if path is None else (primary,):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            sink = RotatingFileHandler(candidate, maxBytes=1_000_000,
                                       backupCount=3, encoding="utf-8")
            _ACTIVE_PATH = candidate
            break
        except OSError:
            continue
    if sink is None:
        return None  # Diagnostics must never prevent the companion from starting.
    sink.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    event_queue: queue.SimpleQueue = queue.SimpleQueue()
    _LOGGER.addHandler(QueueHandler(event_queue))
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    _LISTENER = QueueListener(event_queue, sink)
    _LISTENER.start()
    atexit.register(shutdown_logging)
    return _ACTIVE_PATH


def shutdown_logging() -> None:
    global _LISTENER, _ACTIVE_PATH
    if _LISTENER is not None:
        _LISTENER.stop()
        for sink in _LISTENER.handlers:
            sink.close()
        _LISTENER = None
    for handler in _LOGGER.handlers[:]:
        _LOGGER.removeHandler(handler)
        handler.close()
    _ACTIVE_PATH = None


def log_event(event: str, **fields: int | bool | str | tuple[int, ...]) -> None:
    if not _LOGGER.handlers:
        return
    if not _SAFE_WORD.fullmatch(event):
        raise ValueError("Unsafe diagnostic event name")
    pieces = [event]
    for key, value in fields.items():
        if key not in _SAFE_FIELDS:
            raise ValueError(f"Unsafe diagnostic field: {key}")
        if isinstance(value, str) and not _SAFE_WORD.fullmatch(value):
            raise ValueError(f"Unsafe diagnostic value for {key}")
        if isinstance(value, tuple) and not all(isinstance(item, int) for item in value):
            raise ValueError(f"Unsafe diagnostic value for {key}")
        if not isinstance(value, (int, bool, str, tuple)):
            raise ValueError(f"Unsafe diagnostic value for {key}")
        pieces.append(f"{key}={value}")
    _LOGGER.info(" ".join(pieces))
