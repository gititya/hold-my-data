"""Round-trip between a hand-markup note and eval .jsonl, so labelling never means
counting character offsets by hand.

    to-note   unlabelled .jsonl  ->  markup .md   (edit this in Obsidian)
    to-jsonl  markup .md         ->  labelled .jsonl

Markup is {{ENTITY:exact text}} wrapped around the sensitive value:

    ping {{PERSON:ravi}} on {{IN_PHONE:98765 43210}} re: order 1234567890

Leave a line untouched and it becomes a negative example ("spans": []). Those are half
the point — without them a tool that redacts everything scores perfectly.

The detector is never run here. Spans come from the markup only: labelling with the thing
you are measuring makes the eval circular (PRD section 8).

Usage:
    python scripts/label_note.py to-note eval/labelled/itr_2026.jsonl -o "<vault>/_private/x.md"
    python scripts/label_note.py to-jsonl "<vault>/_private/x.md" -o eval/labelled/itr_2026.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

MARKUP = re.compile(r"\{\{([A-Z_]+):(.+?)\}\}", re.DOTALL)
ITEM = re.compile(r"^\s*-\s+(.*\S)\s*$")

HEADER = """---
project: hold-my-data
status: awaiting my labels
---

# {title}

Wrap every sensitive value in `{{{{ENTITY:the exact text}}}}`. Leave a line alone if there is
nothing sensitive in it — that is a negative example, and it is half the exam.

> [!warning] This file holds real values. It lives in `_private/` because that folder is
> gitignored. Do not move it into the vault proper.

Valid entity names:

{entities}

When done:
`python scripts/label_note.py to-jsonl "{path}" -o eval/labelled/{stem}.jsonl`

---

## Snippets ({n})

"""


def _entity_names(config_dir: Path) -> set:
    data = yaml.safe_load((config_dir / "taxonomy.yaml").read_text(encoding="utf-8"))
    return {e["name"] for e in data["entities"]}


def to_note(args) -> int:
    rows = [json.loads(l) for l in Path(args.input).read_text(encoding="utf-8").splitlines() if l.strip()]
    names = sorted(_entity_names(Path(args.config)))
    out = Path(args.output).expanduser()
    cols = ", ".join(f"`{n}`" for n in names)
    body = "".join(f"- {r['text']}\n" for r in rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        HEADER.format(title=out.stem, entities=cols, path=out, stem=out.stem, n=len(rows)) + body,
        encoding="utf-8",
    )
    print(f"{len(rows)} snippets -> {out}")
    print("Mark them up in Obsidian, then run to-jsonl.")
    return 0


def to_jsonl(args) -> int:
    names = _entity_names(Path(args.config))
    rows, bad = [], []
    for lineno, line in enumerate(Path(args.input).expanduser().read_text(encoding="utf-8").splitlines(), 1):
        m = ITEM.match(line)
        if not m:
            continue
        raw = m.group(1)
        if raw.count("{{") != len(MARKUP.findall(raw)):
            bad.append(f"  line {lineno}: markup did not parse — label must be a bare name "
                       f"like {{{{IN_PAN:value}}}}, no spaces or extra words")
        text, spans, cursor = [], [], 0
        for mark in MARKUP.finditer(raw):
            entity, value = mark.group(1), mark.group(2)
            if entity not in names:
                bad.append(f"  line {lineno}: unknown entity {entity!r}")
            text.append(raw[cursor:mark.start()])
            start = sum(len(t) for t in text)
            text.append(value)
            spans.append({"entity": entity, "start": start, "end": start + len(value)})
            cursor = mark.end()
        text.append(raw[cursor:])
        rows.append({"text": "".join(text), "spans": spans})

    if bad:
        print("Unknown entity names — fix these or add them to taxonomy.yaml:", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        return 1
    if not rows:
        print(f"no '- ' snippet lines found in {args.input}", file=sys.stderr)
        return 1

    out = Path(args.output).expanduser()
    if "eval/labelled" not in out.as_posix():
        print(f"WARNING: {out} is outside eval/labelled/ — real values must never be committed.",
              file=sys.stderr)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    labelled = sum(1 for r in rows if r["spans"])
    per_entity = {}
    for r in rows:
        for s in r["spans"]:
            per_entity[s["entity"]] = per_entity.get(s["entity"], 0) + 1
    for name, n in sorted(per_entity.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}  {name}")
    print(f"\n{len(rows)} examples -> {out}")
    print(f"  {labelled} positive, {len(rows) - labelled} negative")
    if len(rows) - labelled == 0:
        print("  no negatives — precision is untested. Leave some lines unmarked.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("to-note", to_note), ("to-jsonl", to_jsonl)):
        sp = sub.add_parser(name)
        sp.add_argument("input")
        sp.add_argument("-o", "--output", required=True)
        sp.set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
