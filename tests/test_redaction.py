"""End-to-end behaviour on the repo's own config, regex layer only.

Deliberately runs with use_gliner=False so these tests need no model download. The model
layer is scored by `hold-my-data eval --baseline`, not asserted here.
"""

import pytest

from holdmydata import config as cfg
from holdmydata.engine import Redactor

FAKE_ANTHROPIC = "sk-ant-api03-" + "A" * 45
FAKE_GITHUB = "ghp_" + "b" * 36


@pytest.fixture(scope="module")
def redactor():
    return Redactor(cfg.load(), context="secrets", use_gliner=False)


@pytest.fixture(scope="module")
def default_redactor():
    return Redactor(cfg.load(), context="default", use_gliner=False)


def test_redacts_anthropic_key(redactor):
    out = redactor.redact(f"the key is {FAKE_ANTHROPIC} ok")
    assert FAKE_ANTHROPIC not in out
    assert "ANTHROPIC_KEY" in out


def test_redacts_github_token(redactor):
    out = redactor.redact(f"token {FAKE_GITHUB}")
    assert FAKE_GITHUB not in out


def test_redacts_connection_string(redactor):
    secret = "postgres://admin:hunter2@10.0.0.4:5432/prod"
    out = redactor.redact(f"db is {secret}")
    assert "hunter2" not in out


def test_leaves_clean_text_untouched(redactor):
    text = "there is nothing sensitive in this sentence"
    assert redactor.redact(text) == text


def test_consistent_replacement_is_stable_within_a_run(default_redactor):
    out = default_redactor.redact(
        "mail a@x.com then mail a@x.com again, and b@x.com once"
    )
    assert "a@x.com" not in out and "b@x.com" not in out
    assert out.count("[EMAIL_1]") == 2    # same value -> same placeholder, twice
    assert out.count("[EMAIL_2]") == 1    # different value -> different placeholder
    # numbering must follow reading order, not Presidio's back-to-front application order
    assert out.index("[EMAIL_1]") < out.index("[EMAIL_2]")


def test_mapping_does_not_leak_between_runs(default_redactor):
    first = default_redactor.redact("mail zzz@x.com")
    second = default_redactor.redact("mail qqq@x.com")
    assert first == second  # both are the first email of their own run


def test_secrets_context_ignores_email(redactor):
    text = "write to someone@example.com"
    assert redactor.redact(text) == text  # EMAIL is not in the `secrets` context


def test_unknown_context_fails_loudly():
    with pytest.raises(cfg.ConfigError, match="unknown context"):
        Redactor(cfg.load(), context="nope", use_gliner=False)


def test_lease_style_numbers_are_found():
    import holdmydata as hmd

    text = ("Account No.123456789012, Axis. NEFT/RTGS REF No : 987654321098. "
            "Apt Maple A Wing 204. parking No. AL-B204-17.")
    out = hmd.redact_text(text, context="india_only", use_gliner=False)

    for secret in ("123456789012", "987654321098", "204", "B204-17"):
        assert secret not in out


def test_receipt_counts_a_span_two_detectors_found_once():
    from presidio_analyzer import RecognizerResult as R

    from holdmydata.engine import count_spans

    hits = [R("PHONE", 5, 20, 0.7), R("IN_PHONE", 5, 20, 1.0), R("EMAIL", 30, 40, 1.0)]

    assert count_spans(hits) == {"IN_PHONE": 1, "EMAIL": 1}
