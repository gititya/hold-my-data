"""Stable user-data paths for an installed hold-my-data command."""

import os
from pathlib import Path

MODEL_DIR_ENV = "HOLDMYDATA_MODEL_DIR"


def data_dir() -> Path:
    return Path.home() / ".holdmydata"


def model_dir() -> Path:
    configured = os.environ.get(MODEL_DIR_ENV)
    return Path(configured).expanduser() if configured else data_dir() / "models"


def set_default_model_dir() -> Path:
    path = model_dir()
    os.environ.setdefault(MODEL_DIR_ENV, str(path))
    return path
