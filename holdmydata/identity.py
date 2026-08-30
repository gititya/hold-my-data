"""Who "mine" means for the --who mine filter.

One file, per user, stored outside the repo (`~/.holdmydata/identity.yaml`) so it works no
matter which project directory this runs from, and so it never accidentally ends up in a git
repo. Contains real PII by design -- never logged, never printed back in full.

This replaces OPF: instead of a model guessing whether a span is "private," we just check it
against a short list of things the user told us are theirs. Exact-ish matching, no model,
covers every entity type (not just the handful OPF happened to support).
"""

import getpass
import re
from pathlib import Path

import yaml

IDENTITY_PATH = Path.home() / ".holdmydata" / "identity.yaml"

# Which taxonomy entities each identity field is checked against. Anything not listed here
# (secrets, ORGANISATION, CUSTOMER_ID, ...) is not identity-bound -- it is always kept when
# found, in both modes.
FIELD_ENTITIES = {
    "names": {"PERSON"},
    "emails": {"EMAIL"},
    "phones": {"PHONE", "IN_PHONE"},
    "addresses": {"ADDRESS"},
    "ids": {
        "IN_PAN", "IN_AADHAAR", "IN_GSTIN", "IN_VOTER", "IN_PASSPORT",
        "IN_DRIVING_LICENCE", "IN_VEHICLE_REGISTRATION", "IN_UPI", "IN_IFSC",
        "US_SSN", "US_ITIN", "US_PASSPORT", "US_DRIVER_LICENSE", "US_BANK_NUMBER",
        "IBAN_CODE", "CREDIT_CARD",
    },
}

PROMPTS = [
    ("names", "Name and short forms", "comma-separated"),
    ("emails", "Email addresses", "comma-separated"),
    ("phones", "Phone numbers", "comma-separated"),
    ("addresses", "Addresses", "one per entry; blank finishes"),
    ("ids", "Personal ID numbers", "PAN, Aadhaar, SSN, etc.; comma-separated"),
]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _split(raw: str) -> list:
    return [p.strip() for p in raw.split(",") if p.strip()]


def path() -> Path:
    return IDENTITY_PATH


def exists() -> bool:
    return IDENTITY_PATH.exists()


def load() -> dict:
    data = yaml.safe_load(IDENTITY_PATH.read_text()) or {}
    for field in FIELD_ENTITIES:
        data.setdefault(field, [])
    return data


def save(data: dict) -> None:
    IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(yaml.safe_dump(data, sort_keys=False))
    IDENTITY_PATH.chmod(0o600)


def run_onboarding() -> dict:
    print("Optional: my data only")
    print("======================")
    print(
        "Add your details only if you want Hold My Data to distinguish your information "
        "from someone else's. Entries are hidden while you type and stored only on this "
        "Mac. Leave any answer blank to skip it.\n"
    )

    data = {}
    for index, (field, label, hint) in enumerate(PROMPTS, 1):
        print(f"{index}/5  {label} — {hint}")
        if field == "addresses":
            lines = []
            while True:
                line = getpass.getpass("  Hidden entry: ").strip()
                if not line:
                    break
                lines.append(line)
            data[field] = lines
        else:
            raw = getpass.getpass("  Hidden entry: ").strip()
            data[field] = _split(raw)
        print(f"  {len(data[field])} saved\n")

    save(data)
    print("Identity setup saved privately. Rerun hold-my-data setup to change it.\n")
    return data


def ensure() -> dict:
    """Load identity, running onboarding first if this is the first run."""
    if not exists():
        return run_onboarding()
    return load()


def _matches_name(found: str, variants: list) -> bool:
    f = _normalize(found).replace(".", "")
    for v in variants:
        vn = _normalize(v).replace(".", "")
        if not vn:
            continue
        if f == vn or f in vn or vn in f:
            return True
    return False


def _matches_contains(found: str, variants: list) -> bool:
    f = _normalize(found)
    for v in variants:
        vn = _normalize(v)
        if vn and (f in vn or vn in f):
            return True
    return False


def _matches_digits(found: str, variants: list, tail: int = 10) -> bool:
    f = _digits(found)[-tail:]
    if not f:
        return False
    for v in variants:
        vd = _digits(v)[-tail:]
        if vd and vd == f:
            return True
    return False


def is_mine(entity_type: str, value: str, identity: dict) -> bool:
    """Does this found span belong to the user, per their identity file?

    Entity types we have no matching rule for (secrets, ORGANISATION, CUSTOMER_ID, ...) are
    treated as always the user's own -- an API key found in your own text is definitionally
    yours, there is nothing to compare it against.
    """
    for field, entities in FIELD_ENTITIES.items():
        if entity_type not in entities:
            continue
        variants = identity.get(field, [])
        if not variants:
            return False
        if field == "names":
            return _matches_name(value, variants)
        if field == "addresses":
            return _matches_contains(value, variants)
        if field in ("phones",):
            return _matches_digits(value, variants)
        if field == "ids":
            return _matches_digits(value, variants, tail=32) or _matches_contains(
                value.replace(" ", "").replace("-", ""),
                [v.replace(" ", "").replace("-", "") for v in variants],
            )
        # emails and anything else: exact, case-insensitive
        return _normalize(value) in {_normalize(v) for v in variants}
    return True


def filter_mine(results: list, text: str, identity: dict) -> list:
    """Keep only spans that match the user's own identity."""
    return [
        r for r in results
        if is_mine(r.entity_type, text[r.start:r.end], identity)
    ]
