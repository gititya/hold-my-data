"""Eval harness.

Span-level scoring against Adi's own labelled examples. Span-level, not token-level: the
question is "did you find the right thing", not "did you get the right characters".

A hit counts as correct when it overlaps a gold span of the same entity type. Overlap rather
than exact boundaries is deliberate -- a detector that catches "sk-ant-api03-XXXX" but starts
one character late has not failed at the job this tool exists to do.

The false-negative list matters more than the score. Read that section first.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval" / "labelled"


class EvalError(RuntimeError):
    pass


@dataclass
class Example:
    text: str
    spans: list
    source: str


def load_examples(path=None) -> list:
    d = Path(path) if path else EVAL_DIR
    files = sorted(d.glob("*.jsonl")) if d.is_dir() else [d]
    if not files:
        raise EvalError(
            f"no labelled examples in {d}. This is the whole point of the project -- "
            "write 50-100 real examples before trusting any score. See PRD section 7."
        )

    examples = []
    for f in files:
        for i, line in enumerate(f.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvalError(f"{f.name}:{i} is not valid JSON -- {exc}") from exc
            if "text" not in row:
                raise EvalError(f"{f.name}:{i} has no 'text'")
            examples.append(
                Example(text=row["text"], spans=row.get("spans", []), source=f"{f.name}:{i}")
            )
    return examples


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def score(redactor, examples: list) -> dict:
    per_entity = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    false_negatives = []
    false_positives = []

    for ex in examples:
        found = redactor.analyze(ex.text)
        gold = list(ex.spans)
        matched_found = set()

        for g in gold:
            entity = g["entity"]
            hit = None
            for i, f in enumerate(found):
                if i in matched_found:
                    continue
                if f.entity_type == entity and _overlaps(
                    f.start, f.end, g["start"], g["end"]
                ):
                    hit = i
                    break
            if hit is None:
                per_entity[entity]["fn"] += 1
                false_negatives.append(
                    {
                        "source": ex.source,
                        "entity": entity,
                        "start": g["start"],
                        "end": g["end"],
                        # the miss itself is the sensitive value -- record only its shape
                        "length": g["end"] - g["start"],
                    }
                )
            else:
                matched_found.add(hit)
                per_entity[entity]["tp"] += 1

        for i, f in enumerate(found):
            if i not in matched_found:
                per_entity[f.entity_type]["fp"] += 1
                false_positives.append(
                    {
                        "source": ex.source,
                        "entity": f.entity_type,
                        "start": f.start,
                        "end": f.end,
                        "score": round(f.score, 3),
                    }
                )

    report = {"per_entity": {}, "false_negatives": false_negatives,
              "false_positives": false_positives, "n_examples": len(examples)}

    tot = {"tp": 0, "fp": 0, "fn": 0}
    for entity, c in sorted(per_entity.items()):
        report["per_entity"][entity] = _metrics(c)
        for k in tot:
            tot[k] += c[k]
    report["overall"] = _metrics(tot)
    return report


def _metrics(c: dict) -> dict:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def to_markdown(report: dict, label: str) -> str:
    o = report["overall"]
    lines = [
        f"# eval — {label}",
        "",
        f"- date: {date.today().isoformat()}",
        f"- examples: {report['n_examples']}",
        "",
        "## overall",
        "",
        f"| precision | recall | f1 | tp | fp | fn |",
        f"|---|---|---|---|---|---|",
        f"| {o['precision']} | {o['recall']} | {o['f1']} | {o['tp']} | {o['fp']} | {o['fn']} |",
        "",
        "**Recall is the number that matters.** Over-redacting is annoying; under-redacting",
        "is the failure that hurts (PRD section 8).",
        "",
        "## per entity",
        "",
        "| entity | precision | recall | f1 | tp | fp | fn |",
        "|---|---|---|---|---|---|---|",
    ]
    for entity, m in report["per_entity"].items():
        lines.append(
            f"| {entity} | {m['precision']} | {m['recall']} | {m['f1']} "
            f"| {m['tp']} | {m['fp']} | {m['fn']} |"
        )

    lines += ["", "## misses (read this first)", ""]
    if not report["false_negatives"]:
        lines.append("None.")
    else:
        lines.append("| source | entity | offset | length |")
        lines.append("|---|---|---|---|")
        for fn in report["false_negatives"]:
            lines.append(
                f"| {fn['source']} | {fn['entity']} | {fn['start']}:{fn['end']} "
                f"| {fn['length']} |"
            )
        lines.append("")
        lines.append(
            "> Values are deliberately not printed. Open the source line to see the miss."
        )

    lines += ["", "## over-flags", ""]
    if not report["false_positives"]:
        lines.append("None.")
    else:
        lines.append("| source | entity | offset | score |")
        lines.append("|---|---|---|---|")
        for fp in report["false_positives"][:100]:
            lines.append(
                f"| {fp['source']} | {fp['entity']} | {fp['start']}:{fp['end']} "
                f"| {fp['score']} |"
            )
    return "\n".join(lines) + "\n"


def compare(baseline: dict, full: dict, delta_required: float = 0.10) -> str:
    """PRD section 7 second kill rule, as a command rather than a manual comparison."""
    b, f = baseline["overall"]["recall"], full["overall"]["recall"]
    delta = round(f - b, 3)
    verdict = "KEEP" if delta >= delta_required else "REMOVE"
    return (
        f"regex-only recall: {b}\n"
        f"with GLiNER recall: {f}\n"
        f"delta: {delta:+} (required: >= {delta_required})\n"
        f"VERDICT: {verdict} the model layer\n"
    )
