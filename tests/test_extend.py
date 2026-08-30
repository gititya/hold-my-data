"""Runtime taxonomy injection — the capability a host product depends on.

A support product using this component declares its own customers' sensitive data at
runtime. It never edits our config files.
"""

import pytest

import holdmydata as hmd
from holdmydata import config as cfg

ACME = {
    "entities": [
        {
            "name": "ACME_POLICY_NO",
            "description": "an Acme insurance policy number",
            "detect_with": ["regex"],
            "severity": "high",
            "replacement": "[POLICY]",
        }
    ]
}
ACME_PATTERNS = {
    "ACME_POLICY_NO": [{"name": "pol", "regex": r"\bPOL-\d{8}\b", "score": 1.0}]
}


def test_extend_adds_the_callers_entity():
    base = hmd.load_config()
    extended = hmd.extend(base, ACME, ACME_PATTERNS)
    assert "ACME_POLICY_NO" in extended.entities
    assert extended.patterns_for("ACME_POLICY_NO")


def test_extend_does_not_mutate_the_base():
    """Two products in one process must not see each other's taxonomy."""
    base = hmd.load_config()
    before = len(base.entities)
    hmd.extend(base, ACME, ACME_PATTERNS)
    assert len(base.entities) == before
    assert "ACME_POLICY_NO" not in base.entities


def test_extend_keeps_our_baseline_entities():
    extended = hmd.extend(hmd.load_config(), ACME, ACME_PATTERNS)
    assert "ANTHROPIC_KEY" in extended.entities
    assert "IN_PAN" in extended.entities


def test_extend_rejects_redefining_an_existing_entity():
    """Silently changing what a security control means would be undetectable."""
    clash = {
        "entities": [
            {
                "name": "EMAIL",
                "description": "something else entirely",
                "detect_with": ["regex"],
                "severity": "low",
            }
        ]
    }
    with pytest.raises(cfg.ConfigError, match="cannot redefine"):
        hmd.extend(hmd.load_config(), clash, {"EMAIL": [{"regex": "x", "score": 1.0}]})


def test_extend_can_add_a_context():
    extended = hmd.extend(
        hmd.load_config(),
        ACME,
        ACME_PATTERNS,
        contexts={
            "acme": {
                "inherits": "support",
                "entities": ["+ACME_POLICY_NO"],
                "threshold": 0.25,
            }
        },
    )
    assert "ACME_POLICY_NO" in extended.context("acme").entities
    assert "IN_PAN" in extended.context("acme").entities  # inherited from support


def test_extend_validates_the_callers_config():
    bad = {"entities": [{"name": "NOPE", "detect_with": ["regex"]}]}
    with pytest.raises(cfg.ConfigError, match="description"):
        hmd.extend(hmd.load_config(), bad)


def test_from_dicts_needs_no_files():
    """A host product can supply the whole taxonomy, ours included or not."""
    c = cfg.from_dicts(ACME, ACME_PATTERNS)
    assert list(c.entities) == ["ACME_POLICY_NO"]
    assert c.context("default").entities == ["ACME_POLICY_NO"]


def test_injected_entity_actually_redacts():
    extended = hmd.extend(
        hmd.load_config(),
        ACME,
        ACME_PATTERNS,
        contexts={"acme": {"entities": ["ACME_POLICY_NO"], "threshold": 0.4}},
    )
    r = hmd.Redactor(extended, context="acme", use_gliner=False)
    out = r.redact("claim under POL-12345678 filed")
    assert "POL-12345678" not in out
    assert "[POLICY_1]" in out
