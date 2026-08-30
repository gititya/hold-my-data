"""Anonymisation operators.

`ConsistentReplace` is the one genuine addition over stock Presidio (PRD 5b). Presidio's
default `replace` maps every name to <PERSON>, which destroys the structure of the text:
you can no longer tell whether two mentions are the same person. Consistent replacement keeps
"PERSON_1 emailed PERSON_2 twice" readable while leaking nothing.

The mapping lives in memory for one run and is discarded. It is never written to disk --
a persisted mapping is a re-identification key, which is the thing we are trying not to have.
"""

import hashlib
import hmac
import os

from presidio_anonymizer.operators import Operator, OperatorType

HASH_SALT_ENV = "HOLDMYDATA_HASH_SALT"


# Presidio constructs operator instances internally, so instance state does not survive
# across the entities of a single document. The run-scoped mapping therefore lives at module
# level, and `reset_run()` is called once per redaction.
_RUN_COUNTERS = {}
_RUN_SEEN = {}


def reset_run() -> None:
    """Drop the mapping. Called at the start of every redaction, and after every one."""
    _RUN_COUNTERS.clear()
    _RUN_SEEN.clear()


def assign(entity_type: str, value: str, template: str = None) -> str:
    """Return the placeholder for `value`, allocating a new one on first sight.

    Placeholders are always numbered. Unnumbered would be ambiguous the moment a second
    distinct value shows up, and "predictable" beats "tidy" for something you read to check
    whether a leak happened.
    """
    key = (entity_type, value)
    if key not in _RUN_SEEN:
        n = _RUN_COUNTERS.get(entity_type, 0) + 1
        _RUN_COUNTERS[entity_type] = n
        base = template.strip("[]") if template else entity_type
        _RUN_SEEN[key] = f"[{base}_{n}]"
    return _RUN_SEEN[key]


class ConsistentReplace(Operator):
    """Same value -> same placeholder.

    Numbering is in first-appearance order, but that ordering cannot be established here:
    Presidio applies operators back-to-front through the text, so the last match would be
    numbered first. `engine.redact` therefore primes the mapping in reading order before
    anonymising, and this operator only ever looks up what was already assigned.
    """

    def operate(self, text: str, params: dict = None) -> str:
        params = params or {}
        return assign(
            params.get("entity_type", "ENTITY"), text, params.get("replacement")
        )

    def validate(self, params: dict = None) -> None:
        return None

    def operator_name(self) -> str:
        return "consistent"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize


class StableHash(Operator):
    """HMAC-SHA256, stable across runs. Opt-in.

    // WARNING: a stable hash is a weak re-identification vector when the value space is
    // small -- an attacker who can guess candidate values can confirm them by hashing.
    // Only use this when you actually need cross-run correlation, and keep the salt secret.
    """

    def operate(self, text: str, params: dict = None) -> str:
        params = params or {}
        entity_type = params.get("entity_type", "ENTITY")
        salt = os.environ.get(HASH_SALT_ENV)
        if not salt:
            raise ValueError(
                f"strategy 'hash' requires {HASH_SALT_ENV} to be set. Without a secret salt "
                "the hash is trivially reversible by brute force."
            )
        digest = hmac.new(salt.encode(), text.encode(), hashlib.sha256).hexdigest()[:12]
        return f"[{entity_type}_{digest}]"

    def validate(self, params: dict = None) -> None:
        return None

    def operator_name(self) -> str:
        return "stablehash"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize
