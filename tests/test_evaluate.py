"""The scorer itself must be right, or every number it prints is worthless."""

import pytest

from holdmydata import evaluate


class FakeResult:
    def __init__(self, entity_type, start, end, score=0.9):
        self.entity_type, self.start, self.end, self.score = entity_type, start, end, score


class FakeRedactor:
    """Returns canned findings so we score the scorer, not Presidio."""

    def __init__(self, findings):
        self._findings = findings

    def analyze(self, text):
        return self._findings.get(text, [])


def ex(text, spans):
    return evaluate.Example(text=text, spans=spans, source="test:1")


def test_perfect_score():
    examples = [ex("aaa", [{"entity": "EMAIL", "start": 0, "end": 3}])]
    r = evaluate.score(FakeRedactor({"aaa": [FakeResult("EMAIL", 0, 3)]}), examples)
    assert r["overall"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0,
                            "tp": 1, "fp": 0, "fn": 0}


def test_counts_a_miss():
    examples = [ex("aaa", [{"entity": "EMAIL", "start": 0, "end": 3}])]
    r = evaluate.score(FakeRedactor({}), examples)
    assert r["overall"]["recall"] == 0.0
    assert r["overall"]["fn"] == 1
    assert len(r["false_negatives"]) == 1


def test_counts_an_over_flag():
    examples = [ex("aaa", [])]
    r = evaluate.score(FakeRedactor({"aaa": [FakeResult("EMAIL", 0, 3)]}), examples)
    assert r["overall"]["fp"] == 1
    assert r["overall"]["precision"] == 0.0


def test_partial_overlap_counts_as_a_hit():
    """Off-by-one boundaries are not the failure this tool exists to prevent."""
    examples = [ex("aaa", [{"entity": "EMAIL", "start": 0, "end": 10}])]
    r = evaluate.score(FakeRedactor({"aaa": [FakeResult("EMAIL", 1, 9)]}), examples)
    assert r["overall"]["tp"] == 1


def test_wrong_entity_type_is_not_a_hit():
    examples = [ex("aaa", [{"entity": "EMAIL", "start": 0, "end": 3}])]
    r = evaluate.score(FakeRedactor({"aaa": [FakeResult("PERSON", 0, 3)]}), examples)
    assert r["overall"]["tp"] == 0
    assert r["overall"]["fn"] == 1
    assert r["overall"]["fp"] == 1


def test_false_negative_record_omits_the_value():
    """A miss log that prints the missed secret would be its own leak."""
    examples = [ex("secret", [{"entity": "EMAIL", "start": 0, "end": 6}])]
    r = evaluate.score(FakeRedactor({}), examples)
    assert "secret" not in str(r["false_negatives"])
    assert r["false_negatives"][0]["length"] == 6


def test_kill_rule_verdict():
    base = {"overall": {"recall": 0.70}}
    full = {"overall": {"recall": 0.85}}
    assert "KEEP" in evaluate.compare(base, full)
    assert "REMOVE" in evaluate.compare(base, {"overall": {"recall": 0.75}})


def test_empty_eval_dir_is_an_error(tmp_path):
    with pytest.raises(evaluate.EvalError, match="PRD section 7"):
        evaluate.load_examples(tmp_path)


def test_loads_jsonl(tmp_path):
    (tmp_path / "a.jsonl").write_text(
        '// a comment\n{"text": "hi", "spans": []}\n\n{"text": "yo", "spans": []}\n'
    )
    assert len(evaluate.load_examples(tmp_path)) == 2


def test_bad_json_names_the_line(tmp_path):
    (tmp_path / "a.jsonl").write_text('{"text": "ok"}\nnot json\n')
    with pytest.raises(evaluate.EvalError, match="a.jsonl:2"):
        evaluate.load_examples(tmp_path)
