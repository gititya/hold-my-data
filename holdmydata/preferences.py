"""What the setup wizard decided -- which PII categories and OCR languages to run by default.

One file, per user, stored outside the repo (`~/.holdmydata/preferences.yaml`), same pattern
as `identity.py`. Not required: if it doesn't exist, callers fall back to whatever they
already default to (the `default` context, no extra OCR languages).
"""

from pathlib import Path

import yaml

PREFERENCES_PATH = Path.home() / ".holdmydata" / "preferences.yaml"


def path() -> Path:
    return PREFERENCES_PATH


def exists() -> bool:
    return PREFERENCES_PATH.exists()


def load() -> dict:
    data = yaml.safe_load(PREFERENCES_PATH.read_text()) or {}
    data.setdefault("entities", None)
    data.setdefault("ocr_langs", [])
    return data


def save(entities: list, ocr_langs: list) -> None:
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES_PATH.write_text(
        yaml.safe_dump({"entities": entities, "ocr_langs": ocr_langs}, sort_keys=False)
    )
