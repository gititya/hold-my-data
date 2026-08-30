"""Split markdown into snippet candidates and emit an unlabelled .jsonl skeleton.

Spans are ALWAYS empty. This script never suggests them, and must not be changed to:
labelling with the detector you are measuring makes the eval circular (PRD section 8 —
the number has to come from hand-labelled examples).

Usage:
    python scripts/make_eval_skeleton.py ~/Downloads/ITR/md -o eval/labelled/itr.jsonl
    python scripts/make_eval_skeleton.py <dir> --per-file 6 --seed 0
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

MIN_CHARS = 30
MAX_CHARS = 300

# markdown scaffolding that carries no text worth labelling
_SEPARATOR = re.compile(r"^[\s|:\-_=*#]+$")


def _clean_row(line: str) -> str:
    """Turn a markdown table row into readable text."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return " | ".join(c for c in cells if c)


def snippets(md: str):
    """Yield candidate snippets: one per table row, one per paragraph."""
    for block in re.split(r"\n\s*\n", md):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for line in lines:
            if _SEPARATOR.match(line):
                continue
            if line.lstrip().startswith("|"):
                yield _clean_row(line)
        if not any(ln.lstrip().startswith("|") for ln in lines):
            yield " ".join(ln.strip() for ln in lines)


def collect(path: Path, per_file: int, rng: random.Random) -> list:
    out = []
    for md_file in sorted(path.rglob("*.md")):
        seen, kept = set(), []
        for text in snippets(md_file.read_text(encoding="utf-8", errors="replace")):
            text = re.sub(r"\s+", " ", text).strip()
            key = text.lower()
            if not (MIN_CHARS <= len(text) <= MAX_CHARS):
                continue
            if not any(c.isalnum() for c in text) or key in seen:
                continue
            seen.add(key)
            kept.append(text)
        if per_file and len(kept) > per_file:
            kept = rng.sample(kept, per_file)
        out += [{"text": t, "spans": [], "_source": md_file.parent.name} for t in kept]
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="file or directory of .md")
    p.add_argument("-o", "--output", default="eval/labelled/skeleton.jsonl")
    p.add_argument("--per-file", type=int, default=6,
                   help="random snippets per document (0 = keep all)")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    src = Path(args.input).expanduser()
    rows = collect(src, args.per_file, random.Random(args.seed))
    if not rows:
        print(f"no snippets found in {src}", file=sys.stderr)
        return 1

    out = Path(args.output).expanduser()
    if "eval/labelled" not in out.as_posix():
        print(f"WARNING: {out} is outside eval/labelled/ — that is the gitignored path. "
              f"Real values must never be committed.", file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_file = {}
    for r in rows:
        by_file[r["_source"]] = by_file.get(r["_source"], 0) + 1
    for name, n in sorted(by_file.items()):
        print(f"  {n:3d}  {name}")
    print(f"\n{len(rows)} snippets -> {out}")
    print('Every "spans" is empty. Fill them by hand — do not run the detector over this.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
