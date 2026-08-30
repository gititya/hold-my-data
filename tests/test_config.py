"""Config validation must fail loudly at startup, never halfway through a document."""

import pytest

from holdmydata import config as cfg


def write(tmp_path, taxonomy, patterns, rules):
    (tmp_path / "taxonomy.yaml").write_text(taxonomy)
    (tmp_path / "patterns.yaml").write_text(patterns)
    (tmp_path / "rules.yaml").write_text(rules)
    return tmp_path


MIN_TAX = """
entities:
  - name: EMAIL
    description: an email address
    detect_with: [regex]
    severity: high
"""
MIN_PAT = "EMAIL:\n  - name: e\n    regex: '\\\\S+@\\\\S+'\n    score: 0.9\n"
MIN_RULES = "contexts:\n  default:\n    entities: [EMAIL]\n    threshold: 0.4\n"


def test_loads_valid_config(tmp_path):
    c = cfg.load(write(tmp_path, MIN_TAX, MIN_PAT, MIN_RULES))
    assert "EMAIL" in c.entities
    assert c.context("default").threshold == 0.4


def test_ships_config_is_valid():
    """The config in this repo must always load."""
    c = cfg.load()
    assert c.entities and c.patterns and "default" in c.contexts


def test_rejects_entity_missing_description(tmp_path):
    tax = MIN_TAX.replace("    description: an email address\n", "")
    with pytest.raises(cfg.ConfigError, match="description"):
        cfg.load(write(tmp_path, tax, MIN_PAT, MIN_RULES))


def test_rejects_lowercase_entity_name(tmp_path):
    tax = MIN_TAX.replace("EMAIL", "email")
    with pytest.raises(cfg.ConfigError, match="UPPER_SNAKE"):
        cfg.load(write(tmp_path, tax, MIN_PAT, MIN_RULES))


def test_rejects_regex_entity_with_no_pattern(tmp_path):
    with pytest.raises(cfg.ConfigError, match="silently never fire"):
        cfg.load(write(tmp_path, MIN_TAX, "{}\n", MIN_RULES))


def test_rejects_uncompilable_regex(tmp_path):
    bad = "EMAIL:\n  - name: e\n    regex: '([unclosed'\n    score: 0.9\n"
    with pytest.raises(cfg.ConfigError, match="does not compile"):
        cfg.load(write(tmp_path, MIN_TAX, bad, MIN_RULES))


def test_rejects_pattern_for_unknown_entity(tmp_path):
    pat = MIN_PAT + "GHOST:\n  - name: g\n    regex: 'x'\n    score: 1.0\n"
    with pytest.raises(cfg.ConfigError, match="not in taxonomy"):
        cfg.load(write(tmp_path, MIN_TAX, pat, MIN_RULES))


def test_rejects_context_with_unknown_entity(tmp_path):
    rules = "contexts:\n  default:\n    entities: [EMAIL, GHOST]\n"
    with pytest.raises(cfg.ConfigError, match="not in taxonomy"):
        cfg.load(write(tmp_path, MIN_TAX, MIN_PAT, rules))


def test_requires_default_context(tmp_path):
    rules = "contexts:\n  notes:\n    entities: [EMAIL]\n"
    with pytest.raises(cfg.ConfigError, match="'default' context"):
        cfg.load(write(tmp_path, MIN_TAX, MIN_PAT, rules))


def test_inherits_and_applies_deltas(tmp_path):
    tax = MIN_TAX + """
  - name: PERSON
    description: the name of a person
    detect_with: [gliner]
    severity: high
"""
    rules = """
contexts:
  default:
    entities: [EMAIL]
    threshold: 0.4
    strategy: consistent
  notes:
    inherits: default
    entities: [+PERSON, -EMAIL]
    threshold: 0.2
"""
    c = cfg.load(write(tmp_path, tax, MIN_PAT, rules))
    assert c.context("notes").entities == ["PERSON"]
    assert c.context("notes").threshold == 0.2
    assert c.context("notes").strategy == "consistent"  # inherited


def test_rejects_circular_inherits(tmp_path):
    rules = """
contexts:
  default:
    entities: [EMAIL]
  a:
    inherits: b
    entities: [+EMAIL]
  b:
    inherits: a
    entities: [+EMAIL]
"""
    with pytest.raises(cfg.ConfigError, match="circular"):
        cfg.load(write(tmp_path, MIN_TAX, MIN_PAT, rules))


def test_rejects_mixed_delta_and_absolute(tmp_path):
    rules = """
contexts:
  default:
    entities: [EMAIL]
  mixed:
    inherits: default
    entities: [EMAIL, +EMAIL]
"""
    with pytest.raises(cfg.ConfigError, match="mix of plain"):
        cfg.load(write(tmp_path, MIN_TAX, MIN_PAT, rules))


def test_rejects_empty_context(tmp_path):
    rules = """
contexts:
  default:
    entities: [EMAIL]
  empty:
    inherits: default
    entities: [-EMAIL]
"""
    with pytest.raises(cfg.ConfigError, match="would redact nothing"):
        cfg.load(write(tmp_path, MIN_TAX, MIN_PAT, rules))
