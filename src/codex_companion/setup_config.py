"""Persist paths discovered by the explicit Windows setup script."""
from __future__ import annotations

import os

from .config import AppConfig, existing_config_path


def main() -> None:
    executable = os.environ["COMPANION_OLLAMA_EXE"]
    if not os.path.isfile(executable):
        raise FileNotFoundError(executable)
    existed = existing_config_path().is_file()
    config = AppConfig.load()
    config.ollama_executable = executable
    config.ollama_models_dir = os.environ.get("COMPANION_OLLAMA_MODELS", "")
    if not existed or os.environ.get("COMPANION_SET_MODEL") == "1":
        config.ollama_model = os.environ.get("COMPANION_OLLAMA_MODEL", config.ollama_model)
    config.save()


if __name__ == "__main__":
    main()
