"""Persist paths discovered by the explicit Windows setup script."""
from __future__ import annotations

import os

from .config import AppConfig


def main() -> None:
    executable = os.environ["COMPANION_OLLAMA_EXE"]
    if not os.path.isfile(executable):
        raise FileNotFoundError(executable)
    config = AppConfig.load()
    config.backend = "ollama"
    config.ollama_executable = executable
    config.ollama_models_dir = os.environ.get("COMPANION_OLLAMA_MODELS", "")
    config.ollama_model = os.environ.get("COMPANION_OLLAMA_MODEL", config.ollama_model)
    config.save()


if __name__ == "__main__":
    main()
