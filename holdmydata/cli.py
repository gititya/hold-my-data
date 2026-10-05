"""hold-my-data CLI.

`offline` is imported first, before anything that might phone home.
"""

from . import offline  # noqa: F401  -- installs the network guard at import time

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import atomic_io, categories, config as config_mod, preferences
from . import evaluate, identity, logging_safe

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence"

# Presented when no --context/--entities is given, no saved wizard preferences exist, and
# we're talking to a person, not a pipe. Same category list `hold-my-data wizard` uses --
# see categories.py -- so a one-off ad-hoc redaction and the setup wizard's defaults are the
# same menu, not two different classification schemes.
DEFAULT_CATEGORY = "india_full"


def _engine():
    """Imported lazily so `check` can validate config without Presidio installed."""
    from .engine import RedactionFailed, Redactor

    return Redactor, RedactionFailed


def _load(args):
    try:
        return config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_check(args) -> int:
    cfg = _load(args)
    print(f"config OK — {len(cfg.entities)} entities, {len(cfg.patterns)} patterns, "
          f"{len(cfg.contexts)} contexts")
    for name, ctx in sorted(cfg.contexts.items()):
        print(f"  {name}: threshold={ctx.threshold} strategy={ctx.strategy} "
              f"entities={len(ctx.entities)}")
    missing = [
        n for n, e in cfg.entities.items()
        if e.uses_gliner and not e.description
    ]
    if missing:
        print(f"warning: entities with no description: {missing}", file=sys.stderr)
    return 0


def _resolve_context_and_entities(args, cfg):
    """Resolution order: explicit --entities/--context flags win outright; otherwise a saved
    `hold-my-data wizard` preference; otherwise ask a person (multiple categories allowed);
    otherwise (piped, no preference) fall back quietly to the 'default' context so nothing
    that currently works in a pipeline breaks.

    Returns (context_name, entities_or_None). `entities` is always paired with context
    "everything" (the superset) when it comes from categories/preferences, since it is what
    actually narrows the active entity list -- see Redactor.__init__'s own `entities` param.
    """
    if args.entities:
        return (args.context or "everything"), args.entities.split(",")
    if args.context:
        return args.context, None

    if preferences.exists():
        prefs = preferences.load()
        if prefs.get("entities"):
            return "everything", prefs["entities"]

    if not sys.stdin.isatty():
        return "default", None

    print("What's in this file? Pick one or more categories that fit.")
    for i, (_, label, note) in enumerate(categories.CATEGORIES, 1):
        print(f"  {i}. {label}")
        if note:
            print(f"     {note}")
    print(f"  Enter one or more numbers, comma-separated (e.g. 1,3) — "
          f"default: {DEFAULT_CATEGORY}")
    raw = input("  > ").strip()
    if not raw:
        return DEFAULT_CATEGORY, None
    try:
        picks = [int(p.strip()) for p in raw.split(",") if p.strip()]
        if not all(1 <= p <= len(categories.CATEGORIES) for p in picks):
            raise ValueError
    except ValueError:
        print(f"  not a valid choice — using {DEFAULT_CATEGORY}", file=sys.stderr)
        return DEFAULT_CATEGORY, None
    regions = [categories.CATEGORIES[p - 1][0] for p in picks]
    return "everything", categories.entities_for_regions(cfg, regions)


def _resolve_ocr_langs(args) -> tuple:
    if args.ocr_langs:
        return tuple(args.ocr_langs.split(","))
    if preferences.exists():
        langs = preferences.load().get("ocr_langs") or []
        if langs:
            return tuple(langs)
    return ()


def _entity_counts(results) -> dict:
    from .engine import count_spans

    return count_spans(results)


def _print_receipt(counts: dict) -> None:
    if not counts:
        print("redacted: nothing found")
        return
    print("redacted: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def _resolve_who(args) -> str:
    if args.who:
        return args.who
    if not sys.stdin.isatty():
        return "everyone"
    choice = input(
        "Redact everyone's info, or only yours? [everyone/mine] (default: everyone): "
    ).strip().lower()
    return "mine" if choice == "mine" else "everyone"


def cmd_text(args) -> int:
    Redactor, RedactionFailed = _engine()
    cfg = _load(args)
    who = _resolve_who(args)
    context, entities = _resolve_context_and_entities(args, cfg)
    redactor = Redactor(cfg, context=context, use_gliner=not args.no_gliner, entities=entities)

    text = Path(args.input).read_text() if args.input else sys.stdin.read()
    try:
        if who == "mine":
            me = identity.ensure()
            results = identity.filter_mine(redactor.analyze(text), text, me)
            out = redactor.anonymize(text, results)
        else:
            out = redactor.redact(text)
    except RedactionFailed as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    if args.output:
        atomic_io.write_text(Path(args.output), out, force=args.force)
        logging_safe.info(f"wrote {args.output}")
    else:
        sys.stdout.write(out)
    return 0


def cmd_doc(args) -> int:
    """Redact a file to a file. Same detection as `text`, but with a real filename in and
    out -- extension preserved, atomic write, and a receipt of what was found."""
    Redactor, RedactionFailed = _engine()
    cfg = _load(args)
    who = _resolve_who(args)
    context, entities = _resolve_context_and_entities(args, cfg)
    redactor = Redactor(cfg, context=context, use_gliner=not args.no_gliner, entities=entities)

    in_path = Path(args.input)
    out_path = (
        Path(args.output) if args.output
        else in_path.with_name(f"{in_path.stem}.redacted{in_path.suffix}")
    )

    text = in_path.read_text()
    try:
        results = redactor.analyze(text)
        if who == "mine":
            me = identity.ensure()
            results = identity.filter_mine(results, text, me)
        out = redactor.anonymize(text, results)
    except RedactionFailed as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    try:
        atomic_io.write_text(out_path, out, force=args.force)
    except atomic_io.WouldOverwrite as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    logging_safe.info(f"wrote {out_path}")
    print(f"wrote {out_path}")
    _print_receipt(_entity_counts(results))
    return 0


def cmd_setup(args) -> int:
    identity.run_onboarding()
    return 0


def cmd_wizard(args) -> int:
    from . import wizard

    return wizard.run_wizard()


def cmd_download_models(args) -> int:
    """Run the deliberate network-enabled downloader in a separate process.

    Redaction itself keeps the socket guard active. Only this explicit command starts a
    child process with network access enabled.
    """
    flags = []
    if args.text_only:
        flags.append("--text-only")
    elif args.images_only:
        flags.append("--images-only")
    if args.with_indic_ocr:
        flags.append("--with-indic-ocr")
    env = dict(os.environ, HOLDMYDATA_ALLOW_NETWORK="1")
    return subprocess.run(
        [sys.executable, "-m", "holdmydata.model_fetch", *flags], env=env
    ).returncode


def _redact_visual(args, redact) -> int:
    """Shared by `image` and `pdf`: both OCR their input and write a new visual file."""
    who = _resolve_who(args)
    if who == "mine":
        print("REFUSED: --who mine is not supported for images or PDFs yet — it always "
              "redacts everyone's info.", file=sys.stderr)
        return 1

    Redactor, RedactionFailed = _engine()
    cfg = _load(args)
    context, entities = _resolve_context_and_entities(args, cfg)
    redactor = Redactor(cfg, context=context, use_gliner=not args.no_gliner, entities=entities)
    ocr_langs = _resolve_ocr_langs(args)
    counts = {}
    try:
        redact(redactor, args.input, args.output, force=args.force, ocr_langs=ocr_langs,
               counts_out=counts)
    except atomic_io.WouldOverwrite as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    except RedactionFailed as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.output}")
    _print_receipt(counts)
    return 0


def cmd_image(args) -> int:
    from .images import redact_image

    return _redact_visual(args, redact_image)


def cmd_pdf(args) -> int:
    from .pdfs import redact_pdf

    return _redact_visual(args, redact_pdf)


def cmd_eval(args) -> int:
    Redactor, _ = _engine()
    cfg = _load(args)
    try:
        examples = evaluate.load_examples(args.examples)
    except evaluate.EvalError as exc:
        print(f"eval error: {exc}", file=sys.stderr)
        return 2

    context = args.context or "default"  # eval never prompts -- developer tool, must be deterministic
    full = evaluate.score(
        Redactor(cfg, context=context, use_gliner=True), examples
    )

    if args.baseline:
        base = evaluate.score(
            Redactor(cfg, context=context, use_gliner=False), examples
        )
        print(evaluate.compare(base, full))
        report = evaluate.to_markdown(base, "regex only (baseline)")
        report += "\n---\n\n" + evaluate.to_markdown(full, "regex + GLiNER")
        report += "\n---\n\n## kill rule\n\n```\n" + evaluate.compare(base, full) + "```\n"
        label = "baseline-vs-full"
    else:
        report = evaluate.to_markdown(full, f"context={context}")
        label = context

    o = full["overall"]
    print(f"precision={o['precision']} recall={o['recall']} f1={o['f1']} "
          f"(tp={o['tp']} fp={o['fp']} fn={o['fn']})")
    if full["false_negatives"]:
        print(f"{len(full['false_negatives'])} misses — see the report")

    out = Path(args.out) if args.out else EVIDENCE_DIR / f"eval-{label}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"report: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hold-my-data", description="local redaction, my rules")
    p.add_argument("--config", help="config dir (default: ./config)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--context", default=None,
                        help="a name from rules.yaml (default: asks interactively if run "
                             "by a person; 'default' if piped)")
        sp.add_argument("--no-gliner", action="store_true",
                        help="regex only — the PRD baseline")
        sp.add_argument("--who", choices=["everyone", "mine"], default=None,
                        help="redact everyone's info, or only yours (asks if not given)")
        sp.add_argument("--force", action="store_true",
                        help="overwrite an existing output file")
        sp.add_argument("--entities", default=None,
                        help="comma-separated subset of the context's entities to actually "
                             "run (e.g. ADDRESS or PERSON,EMAIL). Default: everything the "
                             "context includes. A model for anything outside this list is "
                             "never even loaded -- this is the real lever for 'only run the "
                             "address model', not just a results filter.")

    sp = sub.add_parser("check", help="validate config and exit")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("setup", help="(re)enter your identity, used by --who mine")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("wizard", help="interactive first-run setup: pick categories, download only what's needed")
    sp.set_defaults(func=cmd_wizard)

    sp = sub.add_parser(
        "download-models",
        help="deliberately download local reading models; redaction itself stays offline",
    )
    group = sp.add_mutually_exclusive_group()
    group.add_argument("--text-only", action="store_true", help="names + addresses")
    group.add_argument("--images-only", action="store_true", help="English OCR")
    sp.add_argument(
        "--with-indic-ocr",
        action="store_true",
        help="also download Hindi + Tamil OCR models",
    )
    sp.set_defaults(func=cmd_download_models)

    sp = sub.add_parser("text", help="redact text (stdin -> stdout by default)")
    common(sp)
    sp.add_argument("-i", "--input")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_text)

    sp = sub.add_parser("doc", help="redact a file to a file (default output: <name>.redacted<ext>)")
    common(sp)
    sp.add_argument("-i", "--input", required=True)
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_doc)

    sp = sub.add_parser("image", help="redact an image")
    common(sp)
    sp.add_argument("-i", "--input", required=True)
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--ocr-langs", default=None,
                    help="extra OCR languages beyond English, comma-separated (e.g. hi,ta). "
                         "Default: none -- most documents don't need it, and each extra "
                         "language is its own resident model (run `fetch_models.py "
                         "--with-indic-ocr` first if you use this).")
    sp.set_defaults(func=cmd_image)

    sp = sub.add_parser("pdf", help="redact a PDF (pages are re-drawn as images; output is flattened)")
    common(sp)
    sp.add_argument("-i", "--input", required=True)
    sp.add_argument("-o", "--output", required=True)
    sp.add_argument("--ocr-langs", default=None, help="same as `image`")
    sp.set_defaults(func=cmd_pdf)

    sp = sub.add_parser("eval", help="score against labelled examples")
    common(sp)
    sp.add_argument("--examples", help="file or dir of .jsonl (default: eval/labelled)")
    sp.add_argument("--baseline", action="store_true",
                    help="also run regex-only and apply the kill rule")
    sp.add_argument("--out", help="report path")
    sp.set_defaults(func=cmd_eval)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging_safe.configure(verbose=args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
