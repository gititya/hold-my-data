"""Line-oriented JSON helper for the macOS app.

The app starts this once and keeps it running, so models load once per session instead of
once per file. One JSON object per line in, one per line out. Requests run one at a time.
Cancel = the app ends this process; writes are atomic, so no half-written file is left.

Requests (stdin):
  {"id": "1", "op": "status"}
  {"id": "2", "op": "redact", "input": PATH, "output": PATH, "categories": ["secrets", ...],
   "ocr_langs": ["hi"], "force": false, "extra_terms": ["Name To Hide"]}
  {"op": "quit"}

Events (stdout), every one carries the request "id":
  {"event": "ready", "version": "0.2.0"}                      (once, at start, no id)
  {"event": "status", "models": {...}, "needs": {...}, "model_bytes": N, "model_dir": PATH}
  {"event": "stage", "stage": "reading" | "hiding", "page": 3, "pages": 20}
  {"event": "done", "kind": "text" | "image" | "pdf", "output": PATH, "counts": {"EMAIL": 2}}
  {"event": "error", "kind": "refused" | "exists" | "unsupported" | "missing" | "failed",
   "message": TEXT}

Never logs or emits file contents. Error messages carry no matched values.
"""

from . import offline  # noqa: F401  -- installs the network guard at import time

import json
import re
import sys
from importlib import metadata
from pathlib import Path

from . import _IMAGE_SUFFIXES, atomic_io, categories, config as config_mod
from .paths import model_dir

_names_dir = "hugmyface0907__gliner-indian-names-v1"
_address_dir = "shiprocket-ai__open-indicbert-indian-address-ner"


def kind_of(path: Path):
    """'pdf' | 'image' | 'text' | None. Text means strict UTF-8 with no NUL bytes."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    try:
        data = path.read_bytes()
        if b"\0" in data:
            return None
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return "text"


def _nonempty(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else 0


def status() -> dict:
    root = model_dir()
    ocr = root / "paddlex_cache" / "official_models"
    names = [p.name for p in ocr.iterdir()] if ocr.is_dir() else []
    cfg = config_mod.load()
    needs = {}
    for region in categories.CATEGORY_KEYS:
        # Only entities that have no pattern fallback count. Phone numbers also use the
        # name model when it is present, but work without it, so "Contact info" stays instant.
        only_model = [
            n for n in categories.entities_for_regions(cfg, [region])
            if not (cfg.entities[n].uses_regex or cfg.entities[n].uses_presidio)
        ]
        n = categories.models_needed(cfg, only_model)
        needs[region] = [k for k, v in (("names", n["gliner"]), ("address", n["address"])) if v]
    return {
        "models": {
            "names": _nonempty(root / _names_dir),
            "address": _nonempty(root / _address_dir),
            "ocr_en": any(re.fullmatch(r"PP-OCRv\d+_.*_rec", n) for n in names),
            "ocr_indic": any(n.startswith("devanagari_") for n in names)
            and any(n.startswith("ta_") for n in names),
        },
        "needs": needs,
        "model_bytes": _dir_bytes(root),
        "model_dir": str(root),
    }


class Helper:
    def __init__(self, out):
        self.out = out
        self._redactor = None
        self._redactor_key = None

    def emit(self, req_id, event, **fields):
        self.out.write(json.dumps({"id": req_id, "event": event, **fields}) + "\n")
        self.out.flush()

    def _redactor_for(self, cats: list, models: dict):
        from .engine import Redactor

        key = (tuple(sorted(cats)), models["names"])
        if key != self._redactor_key:
            if self._redactor is not None:
                self._redactor.close()
            cfg = config_mod.load()
            entities = categories.entities_for_regions(cfg, cats)
            # Same rule as the CLI: the name model runs whenever it is installed.
            self._redactor = Redactor(
                cfg, context="everything", use_gliner=models["names"], entities=entities
            )
            self._redactor_key = key
        return self._redactor

    def redact(self, req):
        rid = req.get("id")
        src, dst = Path(req["input"]), Path(req["output"])
        force = bool(req.get("force"))
        ocr_langs = tuple(req.get("ocr_langs") or ())
        extra_terms = tuple(req.get("extra_terms") or ())
        if not src.is_file():
            return self.emit(rid, "error", kind="missing", message="This file is no longer there.")
        kind = kind_of(src)
        if kind is None:
            return self.emit(rid, "error", kind="unsupported",
                             message="Can't read this file type yet.")

        from .engine import RedactionFailed

        counts = {}
        try:
            if dst.exists() and not force:
                raise atomic_io.WouldOverwrite(f"{dst} already exists")
            self.emit(rid, "stage", stage="reading")
            redactor = self._redactor_for(req["categories"], status()["models"])
            if kind == "text":
                text = src.read_text()
                from .engine import term_hits

                results = redactor.analyze(text) + term_hits(text, extra_terms)
                self.emit(rid, "stage", stage="hiding")
                atomic_io.write_text(dst, redactor.anonymize(text, results), force=force)
                from .engine import count_spans

                counts.update(count_spans(results))
            elif kind == "image":
                from .images import redact_image

                redact_image(redactor, str(src), str(dst), force=force, ocr_langs=ocr_langs,
                             counts_out=counts, extra_terms=extra_terms)
            else:
                from .pdfs import redact_pdf

                redact_pdf(
                    redactor, str(src), str(dst), force=force, ocr_langs=ocr_langs,
                    counts_out=counts, extra_terms=extra_terms,
                    on_page=lambda n, total: self.emit(rid, "stage", stage="reading",
                                                       page=n, pages=total),
                )
        except atomic_io.WouldOverwrite:
            return self.emit(rid, "error", kind="exists",
                             message="A redacted copy already exists.")
        except RedactionFailed as exc:
            return self.emit(rid, "error", kind="refused", message=str(exc))
        except Exception as exc:  # noqa: BLE001 -- report the type only, never the content
            return self.emit(rid, "error", kind="failed", message=type(exc).__name__)
        self.emit(rid, "done", kind=kind, output=str(dst), counts=counts)

    def handle(self, req) -> bool:
        """Returns False to stop the loop."""
        op, rid = req.get("op"), req.get("id")
        if op == "quit":
            return False
        if op == "status":
            self.emit(rid, "status", **status())
        elif op == "redact":
            self.redact(req)
        else:
            self.emit(rid, "error", kind="failed", message=f"unknown op {op!r}")
        return True


def main() -> int:
    # Stray prints from a dependency must not corrupt the protocol: keep the real stdout
    # for events and send everything else to stderr.
    out, sys.stdout = sys.stdout, sys.stderr
    helper = Helper(out)
    try:
        version = metadata.version("hold-my-data")
    except metadata.PackageNotFoundError:
        version = "dev"
    helper.emit(None, "ready", version=version)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            helper.emit(None, "error", kind="failed", message="bad request")
            continue
        if not helper.handle(req):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
