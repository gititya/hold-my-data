"""Plain-language category menu, shared by the CLI's interactive prompt and the setup wizard.

Maps directly onto `taxonomy.yaml`'s `region:` tags -- this is not a second classification
scheme, just friendly labels and space/model notes on top of the one that already exists.
"""

# (region, label, note) -- note explains what it costs (model or not), not what it detects in
# detail; the entity list itself is the detail, this is the "what am I signing up for" summary.
CATEGORIES = [
    ("secrets", "API keys & secrets",
     "Anthropic, OpenAI, GitHub, Slack, Stripe, JWTs, and more -- pattern-matching only, no model, instant."),
    ("global", "Universal contact info",
     "Emails, phone numbers, credit cards, IP addresses -- pattern-matching only, no model, instant."),
    ("india", "Indian ID numbers",
     "PAN, Aadhaar, GSTIN, driving licence, passport, vehicle registration, UPI, IFSC, pincode, "
     "voter ID -- pattern-matching only, no model, instant."),
    ("meaning", "Names, addresses, dates of birth",
     "Needs two downloaded models to read free text (one for names, one for addresses) -- "
     "the biggest download and the slowest category, but the one that catches the things a "
     "fixed pattern can't."),
    ("usa", "US-specific IDs",
     "SSN, driver's licence, passport, bank number -- built in, lightly tested."),
    ("uk", "UK-specific IDs",
     "NINO, NHS number, passport, driving licence, postcode, vehicle registration -- built in, lightly tested."),
    ("medical", "Medical terms",
     "Diseases, medications, procedures, history -- built in, lightly tested."),
]

CATEGORY_KEYS = [c[0] for c in CATEGORIES]


def entities_for_regions(config, regions: list) -> list:
    """Every entity name in `config` tagged with one of `regions` -- the same rule
    rules.yaml's own `regions:` switchboard uses (config.py's `_load_contexts`), exposed here
    so the CLI and the wizard can build an ad-hoc entity list without a matching rules.yaml
    context existing for every possible combination.
    """
    return [name for name, e in config.entities.items() if e.region in regions]


def models_needed(config, entities: list) -> dict:
    """Which downloadable models this entity list actually requires -- gliner and/or the
    address model. Regex/Presidio-backed entities need neither (already on disk with the
    Python packages, no separate download)."""
    needs_gliner = any(config.entities[n].uses_gliner for n in entities if n in config.entities)
    needs_address = any(config.entities[n].uses_address_model for n in entities if n in config.entities)
    return {"gliner": needs_gliner, "address": needs_address}
