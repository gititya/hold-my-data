"""One deliberate model-download command used by the installed setup wizard."""

import argparse
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rich.console import Console

from .paths import MODEL_DIR_ENV, model_dir

ALLOW = "HOLDMYDATA_ALLOW_NETWORK"
_SIZE_HINTS = {
    "gliner": "~1.1GB on disk",
    "address": "~140MB on disk",
    "paddleocr (en)": "~180MB on disk",
    "paddleocr (en+hi+ta)": "~280MB on disk",
}
_LABELS = {
    "gliner": "Name-reading model",
    "address": "Address-reading model",
    "paddleocr (en)": "English photo-reading model",
    "paddleocr (en+hi+ta)": "English, Hindi and Tamil photo-reading models",
}
console = Console(file=sys.__stdout__)


def _run_step(step: int, total: int, name: str, fetch, log_path: Path) -> None:
    label = _LABELS.get(name, name)
    size = _SIZE_HINTS.get(name, "size unknown")
    with log_path.open("a") as log:
        try:
            with console.status(
                f"[cyan][{step}/{total}][/cyan] Downloading {label} [dim]({size})[/dim]",
                spinner="dots",
            ):
                with redirect_stdout(log), redirect_stderr(log):
                    fetch()
        except Exception:
            console.print(f"[red]✗ {label} failed.[/red] [dim]Details: {log_path}[/dim]")
            raise
    console.print(f"[green]✓[/green] [{step}/{total}] {label} ready")


def _fetch_gliner(target: Path) -> None:
    from gliner import GLiNER

    from .recognizers.gliner_recognizer import DEFAULT_MODEL

    model_name = os.environ.get("HOLDMYDATA_GLINER_MODEL", DEFAULT_MODEL)
    dest = target / model_name.replace("/", "__")
    print(f"downloading {model_name} -> {dest}")
    GLiNER.from_pretrained(model_name).save_pretrained(str(dest))


def _fetch_address_model(target: Path) -> None:
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    from .recognizers.address_recognizer import DEFAULT_MODEL

    model_name = os.environ.get("HOLDMYDATA_ADDRESS_MODEL", DEFAULT_MODEL)
    dest = target / model_name.replace("/", "__")
    print(f"downloading {model_name} -> {dest}")
    AutoTokenizer.from_pretrained(model_name).save_pretrained(str(dest))
    AutoModelForTokenClassification.from_pretrained(model_name).save_pretrained(str(dest))


def _fetch_paddleocr(target: Path, langs=("en",)) -> None:
    dest = target / "paddlex_cache"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(dest)
    from paddleocr import PaddleOCR

    for lang in langs:
        print(f"downloading PaddleOCR ({lang}) weights -> {dest}")
        PaddleOCR(use_angle_cls=True, lang=lang)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--text-only", action="store_true", help="GLiNER + address model only")
    group.add_argument("--images-only", action="store_true", help="PaddleOCR (English) only")
    parser.add_argument(
        "--with-indic-ocr",
        action="store_true",
        help="also fetch Hindi + Tamil PaddleOCR weights",
    )
    args = parser.parse_args(argv)

    if os.environ.get(ALLOW) != "1":
        print(
            f"refusing to download without {ALLOW}=1.\n"
            "Downloading is explicit and never a side effect of redaction.",
            file=sys.stderr,
        )
        return 2

    target = model_dir().resolve()
    os.environ[MODEL_DIR_ENV] = str(target)
    target.mkdir(parents=True, exist_ok=True)

    steps = []
    if not args.images_only:
        steps.append(("gliner", lambda: _fetch_gliner(target)))
        steps.append(("address", lambda: _fetch_address_model(target)))
    if not args.text_only:
        langs = ("en", "hi", "ta") if args.with_indic_ocr else ("en",)
        steps.append((f"paddleocr ({'+'.join(langs)})", lambda: _fetch_paddleocr(target, langs)))

    log_path = target.parent / "setup.log"
    log_path.touch(exist_ok=True)
    log_path.chmod(0o600)
    console.print(
        f"\n[bold]Preparing {len(steps)} reading model{'s' if len(steps) != 1 else ''}[/bold] "
        "[dim]— this can take several minutes[/dim]\n"
    )
    for i, (name, fetch) in enumerate(steps, 1):
        _run_step(i, len(steps), name, fetch, log_path)

    console.print("\n[green bold]✓ Downloads complete.[/green bold]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
