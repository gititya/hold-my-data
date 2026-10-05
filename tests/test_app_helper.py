import io
import json

import pytest

from holdmydata import app_helper


@pytest.fixture
def helper(tmp_path, monkeypatch):
    # empty model dir: name model absent, so the run is pattern-only and fast
    monkeypatch.setenv("HOLDMYDATA_MODEL_DIR", str(tmp_path / "models"))
    out = io.StringIO()
    return app_helper.Helper(out), out


def _events(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_kind_of_sorts_files_by_content(tmp_path):
    (tmp_path / "a.md").write_text("hello")
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00\x01")
    (tmp_path / "c.PNG").write_bytes(b"x")
    (tmp_path / "d.pdf").write_bytes(b"x")

    assert app_helper.kind_of(tmp_path / "a.md") == "text"
    assert app_helper.kind_of(tmp_path / "b.bin") is None
    assert app_helper.kind_of(tmp_path / "c.PNG") == "image"
    assert app_helper.kind_of(tmp_path / "d.pdf") == "pdf"


def test_status_reports_missing_models_and_only_model_backed_needs(helper):
    s = app_helper.status()

    assert s["models"] == {"names": False, "address": False, "ocr_en": False, "ocr_indic": False}
    assert s["needs"]["global"] == []
    assert set(s["needs"]["meaning"]) == {"names", "address"}
    assert s["needs"]["india"] == []


def test_text_file_is_redacted_and_counted(helper, tmp_path):
    h, out = helper
    src, dst = tmp_path / "n.txt", tmp_path / "n.redacted.txt"
    src.write_text("mail alex@example.com now")

    h.redact({"id": "1", "input": str(src), "output": str(dst), "categories": ["global"]})

    done = _events(out)[-1]
    assert done["event"] == "done" and done["kind"] == "text"
    assert done["counts"] == {"EMAIL": 1}
    assert "alex@example.com" not in dst.read_text()


def test_existing_output_needs_force(helper, tmp_path):
    h, out = helper
    src, dst = tmp_path / "n.txt", tmp_path / "n.redacted.txt"
    src.write_text("mail alex@example.com")
    dst.write_text("old")

    req = {"id": "1", "input": str(src), "output": str(dst), "categories": ["global"]}
    h.redact(req)
    assert _events(out)[-1]["kind"] == "exists" and dst.read_text() == "old"

    h.redact({**req, "force": True})
    assert _events(out)[-1]["event"] == "done" and dst.read_text() != "old"


def test_unreadable_and_missing_files_are_reported_not_touched(helper, tmp_path):
    h, out = helper
    binary = tmp_path / "x.key"
    binary.write_bytes(b"\x00\x01\x02")
    dst = tmp_path / "out"

    h.redact({"id": "1", "input": str(binary), "output": str(dst), "categories": ["global"]})
    h.redact({"id": "2", "input": str(tmp_path / "gone.txt"), "output": str(dst),
              "categories": ["global"]})

    kinds = [e["kind"] for e in _events(out) if e["event"] == "error"]
    assert kinds == ["unsupported", "missing"] and not dst.exists()


def test_extra_terms_are_hidden_even_when_not_detected(helper, tmp_path):
    h, out = helper
    src, dst = tmp_path / "n.txt", tmp_path / "n.redacted.txt"
    src.write_text("Ask Zorblax\nQuimby about it. zorblax quimby again.")

    h.redact({"id": "1", "input": str(src), "output": str(dst), "categories": ["global"],
              "extra_terms": ["Zorblax Quimby"]})

    done = _events(out)[-1]
    assert done["event"] == "done" and done["counts"] == {"PERSON": 2}
    assert "uimby" not in dst.read_text()
