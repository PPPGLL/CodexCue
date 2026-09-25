from __future__ import annotations

import json
import math
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:4b-instruct"
OLLAMA_MODEL_CHOICES = (
    ("qwen3:1.7b", "model_light"),
    (DEFAULT_OLLAMA_MODEL, "model_default"),
    ("qwen3:8b", "model_large"),
)
KEYRING_SERVICE = "codexcue"
LEGACY_KEYRING_SERVICE = "codex-composer-companion"


def default_config_path() -> Path:
    if os.environ.get("CODEXCUE_DATA_DIR"):
        return Path(os.environ["CODEXCUE_DATA_DIR"]) / "config.json"
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    primary = base / "CodexCue" / "config.json"
    portable = portable_config_path()
    try:
        if portable.is_file() and (not primary.is_file()
                                   or portable.stat().st_mtime_ns > primary.stat().st_mtime_ns):
            return portable
    except OSError:
        pass
    return primary


def legacy_config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "CodexComposerCompanion" / "config.json"


def existing_config_path() -> Path:
    current = default_config_path()
    if os.environ.get("CODEXCUE_DATA_DIR"):
        return current
    return current if current.is_file() else (
        legacy_config_path() if legacy_config_path().is_file() else current)


def get_cloud_key(url: str) -> str:
    import keyring

    return (keyring.get_password(KEYRING_SERVICE, url)
            or keyring.get_password(LEGACY_KEYRING_SERVICE, url) or "")


def portable_config_path() -> Path:
    return Path(sys.executable).resolve().parents[2] / ".local" / "companion-config.json"


def default_sessions_root() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"


@dataclass
class AppConfig:
    backend: str = "ollama"
    ollama_url: str = DEFAULT_OLLAMA_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_executable: str = ""
    ollama_models_dir: str = ""
    cloud_base_url: str = ""
    cloud_model: str = ""
    enabled: bool = True
    model_idle_seconds: int = 60
    request_timeout_seconds: float = 8.0

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = path or existing_config_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("Configuration must be an object")
            defaults = cls()
            values = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            for key, value in values.items():
                expected = type(getattr(defaults, key))
                if expected is float:
                    valid = type(value) in (int, float) and math.isfinite(value)
                else:
                    valid = type(value) is expected
                if not valid:
                    raise ValueError("Invalid configuration field type")
            result = cls(**values)
            if (result.backend not in {"ollama", "cloud"}
                    or not 0 <= result.model_idle_seconds <= 3600
                    or not 0 < result.request_timeout_seconds <= 120):
                raise ValueError("Invalid configuration value")
            return result
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
            # Never destroy a damaged file just to make the app start. A later
            # explicit save replaces it, while the byte-for-byte backup remains.
            backup = path.with_name(f"{path.name}.corrupt-{uuid4().hex[:12]}.bak")
            try:
                shutil.copy2(path, backup)
            except OSError:
                backup = None
            recovered = cls(enabled=False)
            recovered.recovery_required = True
            recovered.recovery_backup = backup
            return recovered

    def save(self, path: Path | None = None) -> None:
        def write(target: Path) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)

        target = path or default_config_path()
        try:
            write(target)
        except OSError:
            if path is not None or os.environ.get("CODEXCUE_DATA_DIR"):
                raise
            write(portable_config_path())
