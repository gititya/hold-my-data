---
document: hold-my-data product integration contract
contract_version: "0.1-draft"
integration_status: not_ready_for_external_products
source_repository: https://github.com/gititya/hold-my-data
supported_host_language: Python
---

# Product integration contract

This is the source of truth for putting Hold My Data inside another product.
It is written for both people and coding agents.

## Read this first

Hold My Data works today as a local command-line tool and as Python code run from this
repository. It is **not yet ready to be installed as a normal dependency by another
product**.

One product-specific privacy feature still needs to be built here:

1. Add a protected-terms input so product names such as `Option S` are not mistaken for PII.

The product using Hold My Data also has work to do: it must call the redactor before any
external AI service, provide its product-specific privacy profile, and stop processing if
redaction fails.

## What Hold My Data is

Hold My Data is a local privacy layer. It takes raw text or an image and replaces detected
personal information and secrets with labels or stable placeholders.

It provides:

- pattern and checksum detection for fixed-format data such as email addresses, IDs, and
  API keys;
- local model detection for names, dates of birth, and Indian addresses;
- OCR followed by the same detection for images;
- preset privacy contexts, including `support`;
- runtime extension through `holdmydata.extend()` for a product's own sensitive formats;
- fail-closed behavior: a failed detector raises `RedactionFailed` instead of returning
  partly cleaned text.

It does not provide:

- customer-support routing, replies, conversation state, or storage;
- a hosted API;
- a safe way today to exempt product vocabulary from redaction;
- a released product adapter for protected terms.

See [README.md](README.md) for detector coverage, measured accuracy, model sizes, and known
limits. Do not copy those values into a consumer repository; they will change as this
project improves.

## Ownership

| Owner | Responsibility |
|---|---|
| Hold My Data | Detect and replace PII; validate privacy configuration; fail closed |
| Product adapter/profile | Declare product-specific sensitive formats and protected product terms |
| Consuming product | Put redaction at the correct boundary, reuse the redactor, handle failures, and test the integration |
| Support router/state service | Receive only cleaned text; never contain product-specific PII rules |

For the current support system, Voice Support is the consuming product and the privacy
boundary. Support State Core should receive only text that Voice Support has already
cleaned.

## Required data flow

```text
raw customer text
    -> Voice Support receives it
    -> Hold My Data redacts it locally
    -> cleaned text goes to the model, router, logs, and audit records
```

If Hold My Data fails, raw text must not continue to any model, router, log, or audit
record.

## Product privacy profile

Each product should own one versioned, checksummed privacy profile in its trusted product
adapter. A browser, customer message, or other untrusted input must never set this profile.

Target shape:

```yaml
schema_version: "1"
product_id: muesli
base_context: support

sensitive_entities:
  - name: MUESLI_ORDER_ID
    description: a Muesli customer order number
    detect_with: [regex]
    severity: high

patterns:
  MUESLI_ORDER_ID:
    - name: muesli_order
      regex: '\bORD-[0-9]{8}\b'
      score: 1.0

protected_terms:
  - Option S
  - MFA
```

The meanings are:

- `sensitive_entities` and `patterns`: extra customer data that must be redacted. The
  existing `holdmydata.extend()` API supports these today.
- `protected_terms`: trusted product vocabulary that must remain readable. This is
  **planned and not implemented** in Hold My Data today.

Protected terms are not a general PII allowlist. Never put a customer's name, email,
address, phone number, ID, or other personal value in this list.

## Current Python interface

The existing source-level interface is:

```python
import holdmydata as hmd

config = hmd.extend(
    hmd.load_config(),
    taxonomy={
        "entities": [
            {
                "name": "MUESLI_ORDER_ID",
                "description": "a Muesli customer order number",
                "detect_with": ["regex"],
                "severity": "high",
            }
        ]
    },
    patterns={
        "MUESLI_ORDER_ID": [
            {
                "name": "muesli_order",
                "regex": r"\bORD-[0-9]{8}\b",
                "score": 1.0,
            }
        ]
    },
    contexts={
        "muesli_support": {
            "inherits": "support",
            "entities": ["+MUESLI_ORDER_ID"],
            "threshold": 0.25,
            "strategy": "consistent",
        }
    },
)

redactor = hmd.Redactor(config, context="muesli_support")

def scrub_customer_text(raw_text: str) -> str:
    # Reuse this redactor for every request. RedactionFailed must stop the request.
    return redactor.redact(raw_text)

def shutdown() -> None:
    redactor.close()
```

A long-running product should create one `Redactor`, reuse it for requests, and call
`close()` during shutdown. Creating a new one for every message reloads costly detectors
and increases response time.

The example does not include `protected_terms` because that interface does not exist yet.

## Installation

### Current state

The repository is a versioned Python package with a `hold-my-data` command. Release `v0.1.0`
installs through `uv tool` using the exact tagged source archive shown in [README.md](README.md).
That makes the command installable; it does not make the unresolved protected-terms product
contract safe for a support integration.

### Required release state

Before another product relies on Hold My Data, this repository must provide:

1. an installable Python package;
2. a clear version number;
3. a tagged release or wheel;
4. an exact, pinned install instruction;
5. a compatibility test that runs from a clean environment.

The consumer must pin an exact version. It must not copy Hold My Data source files into its
own repository or install from an unpinned branch.

## Runtime needs

- Python 3.13 for the current build.
- Install dependencies declared in [pyproject.toml](pyproject.toml); repository development
  also uses [requirements.txt](requirements.txt).
- No model for pattern-only detection.
- Local model files for names, addresses, dates of birth, or image OCR.
- Enough memory for the selected detectors; see [README.md](README.md#what-it-uses-and-what-it-costs).

The command-line interface installs a socket-level network block. The public library API
does not import that block automatically. Before product use, the integration must prove
that redaction cannot make an outbound network call without also blocking the consuming
product's legitimate network calls. This boundary is unresolved today.

## Work required in each repository

### Hold My Data

1. Define and implement `protected_terms` with strict validation.
2. Define the stable error and result contract for consumers.
3. Define a safe network-isolation method for library use.
4. Publish integration tests.

### Consuming product, such as Voice Support

1. Add the pinned Hold My Data dependency.
2. Read the trusted product privacy profile from the registered adapter.
3. Build and reuse one redactor for each registered product profile.
4. Redact before any external model, router, log, or audit write.
5. Stop the request if redaction fails.
6. Measure redaction time and total request time.

### Product adapter/profile

1. Define its own sensitive ID formats.
2. Define only genuine product terms that must survive redaction.
3. Version and checksum the profile.
4. Reject runtime changes from customers or browser input.

## Acceptance checks

An integration is complete only when all of these pass:

- A clean environment installs one pinned Hold My Data release.
- Known names, emails, phone numbers, addresses, IDs, and secrets do not reach downstream
  services in raw form.
- Product-specific customer IDs are redacted.
- Registered product terms such as `Option S` and `MFA` remain unchanged.
- A customer cannot add a protected term.
- A redaction failure prevents every downstream model call, route, log, and audit write.
- Redaction makes no network connection.
- The host reuses loaded detectors instead of rebuilding them for every message.
- Product routing gives the same result when it receives the expected cleaned text.
- Tests cover both false negatives and harmful false positives for that product.

## Known support regression

The integration must fix and preserve the verified Wispr Reachout failure. Presidio changed
`Option S` to `<ORGANIZATION>` and caused
`support.unsupported_request -> handoff` instead of
`shortcuts.change_trigger_key -> guide`. It also changed `Windows` to `<NRP>` in a separate
live request.

The release gate is the complete production-shaped chain, including speech-to-text
capitalization and punctuation. A lowercase text fixture is not enough. Do not change the
validated product registration to fit damaged privacy output, and do not disable privacy for
real customer speech.

## Instructions for a coding agent

1. Read this file and [README.md](README.md).
2. Check `integration_status` at the top. Do not claim the integration is ready while it is
   `not_ready_for_external_products`.
3. Complete the Hold My Data work above in this repository and release an exact version.
4. In the consumer repository, implement the required data flow and trusted profile input.
5. Run every acceptance check above and save the results.
6. Mark the integration ready only after all checks pass on a clean machine.
