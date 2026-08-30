"""Optional plain-text setup for model choices and saved preferences."""

import os
import subprocess
import sys

from . import categories, config as config_mod, preferences

_MODEL_SIZE = {
    "gliner": "~1.1GB",
    "address": "~140MB",
    "ocr_en": "~180MB",
    "ocr_indic": "~100MB more",
}


def _ask_multi(prompt: str, options: list, allow_none: bool = False) -> list:
    print(f"\n{prompt}")
    for index, (_, label, note) in enumerate(options, 1):
        print(f"  {index}. {label}")
        if note:
            print(f"     {note}")
    print("  Enter one or more numbers, comma-separated (for example: 1,3).")
    while True:
        raw = input("  > ").strip()
        if not raw and allow_none:
            return []
        try:
            picks = [int(value.strip()) for value in raw.split(",") if value.strip()]
        except ValueError:
            picks = []
        if picks and all(1 <= value <= len(options) for value in picks):
            return [options[value - 1][0] for value in dict.fromkeys(picks)]
        print("  Choose at least one valid number.")


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("  Enter y or n.")


def run_wizard() -> int:
    print("Hold My Data setup")
    print("==================")
    print("This saves privacy choices on this Mac and downloads only the models selected.")

    file_types = _ask_multi(
        "What will you redact?",
        [
            ("text", "Text and documents", "typed text and extracted text files"),
            ("images", "Photos and scanned images", "needs a local OCR model"),
        ],
    )
    picked_regions = _ask_multi(
        "Which categories should be protected?",
        categories.CATEGORIES,
    )

    ocr_langs = []
    if "images" in file_types and _ask_yes_no(
        "Do the images contain Hindi or Tamil as well as English?"
    ):
        ocr_langs = ["hi", "ta"]

    config = config_mod.load()
    entities = categories.entities_for_regions(config, picked_regions)
    needs = categories.models_needed(config, entities)

    downloads = []
    if needs["gliner"]:
        downloads.append(f"name model ({_MODEL_SIZE['gliner']})")
    if needs["address"]:
        downloads.append(f"address model ({_MODEL_SIZE['address']})")
    if "images" in file_types:
        downloads.append(f"English OCR ({_MODEL_SIZE['ocr_en']})")
        if ocr_langs:
            downloads.append(f"Hindi and Tamil OCR ({_MODEL_SIZE['ocr_indic']})")

    print("\nSummary")
    print(f"  Categories: {', '.join(picked_regions)}")
    print(f"  Entity types: {len(entities)}")
    print(f"  Downloads: {', '.join(downloads) if downloads else 'none'}")
    print("  Model downloads may take several minutes. Redaction stays offline afterward.")

    if not _ask_yes_no("Save these choices and continue?", default=True):
        print("Cancelled. Nothing was downloaded or saved.")
        return 1

    flags = []
    if "text" in file_types and "images" not in file_types:
        flags.append("--text-only")
    elif "images" in file_types and "text" not in file_types:
        flags.append("--images-only")
    if ocr_langs:
        flags.append("--with-indic-ocr")

    if downloads:
        env = dict(os.environ, HOLDMYDATA_ALLOW_NETWORK="1")
        result = subprocess.run(
            [sys.executable, "-m", "holdmydata.model_fetch", *flags], env=env
        )
        if result.returncode != 0:
            print("Model download failed. Preferences were not saved.", file=sys.stderr)
            return result.returncode

    preferences.save(entities=entities, ocr_langs=ocr_langs)
    print(f"\nSetup complete. Preferences saved to {preferences.path()}.")
    print("You can close this window or run a redaction command.")
    return 0
