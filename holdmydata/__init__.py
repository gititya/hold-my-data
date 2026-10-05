"""hold-my-data — local redaction, my rules.

Public API for host products. A support tool integrating this does not edit our config
files; it declares its own sensitive data at runtime:

    import holdmydata as hmd

    cfg = hmd.extend(
        hmd.load_config(),
        taxonomy={"entities": [
            {"name": "ACME_POLICY_NO",
             "description": "an Acme insurance policy number",
             "detect_with": ["regex", "gliner"],
             "severity": "high"},
        ]},
        patterns={"ACME_POLICY_NO": [
            {"name": "acme_pol", "regex": r"\\bPOL-\\d{8}\\b", "score": 1.0},
        ]},
        contexts={"acme": {"inherits": "default",
                           "entities": ["+ACME_POLICY_NO"],
                           "threshold": 0.25}},
    )
    redactor = hmd.Redactor(cfg, context="acme")
    clean = redactor.redact(ticket_body)

`extend` never mutates the config it is given, so two products in one process cannot see or
corrupt each other's taxonomy.
"""

from .paths import set_default_model_dir

set_default_model_dir()

from .config import Config, ConfigError, extend, from_dicts
from .config import load as load_config

__all__ = [
    "Config",
    "ConfigError",
    "Redactor",
    "RedactionFailed",
    "extend",
    "from_dicts",
    "load_config",
    "redact_text",
    "redact_file",
]

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}


def __getattr__(name):
    """Engine imports pull in Presidio and torch -- keep them off the import path until
    something actually needs a redactor (so `check` stays fast and dependency-light)."""
    if name in ("Redactor", "RedactionFailed"):
        from . import engine

        return getattr(engine, name)
    raise AttributeError(name)


def redact_text(text: str, context: str = "default", who: str = "everyone", use_gliner: bool = True,
                 entities: list = None) -> str:
    """In-process text redaction for a host app -- one call, no CLI/subprocess involved.

    `entities`, if given (e.g. ["ADDRESS"]), builds a Redactor scoped to only those entities --
    a recognizer for anything outside that list is never constructed, so its model never
    loads. See `Redactor.__init__` in engine.py for why this is the point of control, not a
    per-call filter after the fact.

    Raises RedactionFailed (nothing partially redacted) rather than returning a guess.
    """
    from . import engine, identity

    cfg = load_config()
    redactor = engine.Redactor(cfg, context=context, use_gliner=use_gliner, entities=entities)
    if who == "mine":
        me = identity.ensure()
        results = identity.filter_mine(redactor.analyze(text), text, me)
        return redactor.anonymize(text, results)
    return redactor.redact(text)


def redact_file(in_path, out_path=None, context: str = "default", who: str = "everyone",
                 force: bool = False, use_gliner: bool = True, ocr_langs: tuple = (),
                 entities: list = None) -> str:
    """Redact a file to a new file. Dispatches on extension: PDFs and image formats go through OCR,
    everything else is treated as plain text. Returns the path written.

    `entities`, if given (e.g. ["ADDRESS"]), builds a Redactor scoped to only those entities --
    see `redact_text` above for why this (not a filter on the analyze call) is what actually
    controls which models load.

    `ocr_langs` -- extra OCR languages beyond English for image files (e.g. ("hi", "ta")).
    Defaults to none; see `holdmydata.images.redact_image` for why this is opt-in, not automatic.
    """
    from pathlib import Path

    from . import atomic_io, engine, identity

    in_path = Path(in_path)
    out_path = Path(out_path) if out_path else in_path.with_name(f"{in_path.stem}.redacted{in_path.suffix}")
    cfg = load_config()
    redactor = engine.Redactor(cfg, context=context, use_gliner=use_gliner, entities=entities)

    if in_path.suffix.lower() == ".pdf":
        if who == "mine":
            raise ValueError("who='mine' is not supported for PDFs yet -- always redacts everyone's info")
        from .pdfs import redact_pdf

        return redact_pdf(redactor, str(in_path), str(out_path), force=force, ocr_langs=ocr_langs)

    if in_path.suffix.lower() in _IMAGE_SUFFIXES:
        if who == "mine":
            raise ValueError("who='mine' is not supported for images yet -- always redacts everyone's info")
        from .images import redact_image

        return redact_image(redactor, str(in_path), str(out_path), force=force, ocr_langs=ocr_langs)

    text = in_path.read_text()
    results = redactor.analyze(text)
    if who == "mine":
        me = identity.ensure()
        results = identity.filter_mine(results, text, me)
    out = redactor.anonymize(text, results)
    atomic_io.write_text(out_path, out, force=force)
    return str(out_path)
