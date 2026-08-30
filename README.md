# Hold My Data

Local redaction for text, documents, and images. Hold My Data finds personal information
and secrets, replaces text with labels such as `[EMAIL_1]`, and paints solid boxes over
matching text in images. Redaction runs on your computer. It has no cloud API.

The project combines:

- patterns and checksums for fixed formats such as email, PAN, Aadhaar, GSTIN, and API keys;
- Presidio recognizers for supported national identifiers;
- a local GLiNER model for names and dates of birth;
- a local Indian-address model; and
- local PaddleOCR for images.

Model download is a separate command that needs internet access. Once the models are on
disk, Hold My Data blocks outbound network connections while redacting.

> Hold My Data reduces risk; it cannot promise that every item will be found. Review a
> redacted file before sharing it, especially when the source is a blurry photo or scan.

## Install on a Mac

Requirements: macOS, Git, and about 1.6 GB of free disk space for the program. Python is
installed inside Hold My Data's private environment.

```bash
brew install uv
git clone https://github.com/gititya/hold-my-data.git
cd hold-my-data
uv tool install --python 3.13 .
hold-my-data check
```

No GitHub account is needed to clone a public repository. If `hold-my-data` is not found
after installation, run `uv tool update-shell`, close Terminal, and open it again.

You can also install the fixed v0.1.1 release without cloning:

```bash
brew install uv
uv tool install --python 3.13 \
  "hold-my-data @ https://github.com/gititya/hold-my-data/archive/refs/tags/v0.1.1.tar.gz"
hold-my-data check
```

## Prompt for Codex, Claude Code, or Cursor

Paste this into your coding agent:

```text
Install Hold My Data from https://github.com/gititya/hold-my-data on this Mac.
Use the repository's documented clone + uv tool install path. Do not build a GUI and do
not use the interactive wizard. Run `hold-my-data check`, then test command-line redaction
with fake email and phone data only. Ask me before downloading optional models. If I approve
models, use `hold-my-data download-models` with the smallest flags that match my needs.
Tell me what was installed, where it was installed, and the exact command I should use next.
```

## Choose which models to download

Pattern-based items work immediately. Download models only for the content you need:

```bash
hold-my-data download-models --text-only     # names, dates of birth, addresses
hold-my-data download-models --images-only   # English photos and scans
hold-my-data download-models                 # both of the above

# Optional: add Hindi and Tamil OCR
hold-my-data download-models --images-only --with-indic-ocr
```

These are the exact model families pulled by the current release:

| Purpose | Model | Measured disk use |
|---|---|---:|
| Names and dates of birth | `hugmyface0907/gliner-indian-names-v1` | 1.1 GB |
| Indian addresses | `shiprocket-ai/open-indicbert-indian-address-ner` | 140 MB |
| English image reading | PaddleOCR v6 detection, recognition, orientation, and document models | 177 MB |
| Hindi and Tamil image reading | PaddleOCR language models, added to English | about 100 MB more |

Measured on a clean install on 30 August 2026:

| Setup | Approximate total disk use | Download time on the test connection |
|---|---:|---:|
| Program only | 1.6 GB | 24 seconds |
| Program + English OCR | 1.8 GB | 68 seconds more |
| Program + text models | 2.8 GB | 92 seconds more |
| Program + all English models | 3.0 GB | about 3 minutes total |
| Program + English, Hindi, and Tamil models | 3.1 GB | 18 seconds more on the test connection |

Network speed and model hosts can make these downloads take several minutes. The first
model-backed text run took 16 seconds in the clean test. A clear synthetic image took 13
seconds; the real 19-image evaluation averaged 53.9 seconds per image. Difficult scans can
take more than 100 seconds. Running every model may need about 4 GB of free memory.

## Use from Terminal

Use fake data for a first check:

```bash
printf 'Email alex@example.com or call +1 415 555 0134' | \
  hold-my-data text --who everyone --entities EMAIL,PHONE --no-gliner
```

Redact a text or Markdown file. The original is kept:

```bash
hold-my-data doc -i notes.md --who everyone --context india_full
# writes notes.redacted.md
```

Redact an image after downloading the OCR model:

```bash
hold-my-data image -i photo.png -o photo.redacted.png \
  --who everyone --context india_full
```

Hold My Data does not read PDF files directly yet. Extract a PDF to text or convert its
pages to images first.

### Redact only your own information

This mode is optional. It stores the details you enter only in
`~/.holdmydata/identity.yaml` on your Mac.

```bash
hold-my-data setup
hold-my-data doc -i notes.md --who mine --context india_full
```

Normal redaction does not ask for or require your personal details.

## Use as a Python library

Hold My Data is a local Python library, not a hosted API. Install it into a project from the
clone:

```bash
git clone https://github.com/gititya/hold-my-data.git
cd hold-my-data
uv venv --python 3.13
source .venv/bin/activate
uv pip install .
```

Then call it in the same Python process:

```python
import holdmydata as hmd

clean = hmd.redact_text(
    "Contact alex@example.com",
    entities=["EMAIL"],
    use_gliner=False,
)

output_path = hmd.redact_file(
    "notes.md",
    context="india_full",
)
```

An integrating product can add its own entity types at runtime with `holdmydata.extend()`.
See [INTEGRATION.md](INTEGRATION.md) for the integration contract.

## What it detects, and how well

Precision means: of everything flagged, how much was correct. Recall means: of everything
that should have been flagged, how much was found. This project favors recall because a
false alarm is safer than leaked personal information.

The current text evaluation contains 1,051 labelled examples. Overall precision is
**0.701**, recall is **0.990**, and F1 is **0.821**. The raw labelled files are not public
because some contain real personal documents; the evaluation code is included.

### Measured text results

| Entity | Precision | Recall | Labelled positives |
|---|---:|---:|---:|
| Person name | 0.912 | 0.995 | 910 |
| Indian PAN | 1.000 | 1.000 | 19 |
| Indian GSTIN | 1.000 | 1.000 | 4 |
| Indian Aadhaar | 1.000 | 1.000 | 3 |
| Indian voter ID | 1.000 | 1.000 | 3 |
| Indian vehicle registration | 1.000 | 1.000 | 4 |
| Indian passport | 0.625 | 1.000 | 5 |
| Indian phone | 1.000 | 1.000 | 2 |
| Email | 1.000 | 1.000 | 4 |
| Anthropic, OpenAI, OpenRouter, and ElevenLabs keys combined | 1.000 | 1.000 | 11 |
| Address | 0.143 | 1.000 | 6 |
| Date of birth | 0.015 | 1.000 | 4 |
| General phone | 0.250 | 1.000 | 1 |
| Customer ID | 0.000 | not measured | 0 |

Low address and date precision means the tool hides too much, not that the measured items
leaked. Counts below about 20 are too small to support a strong accuracy claim.

A separate small US-format smoke set had one positive per type: US driver licence, ITIN,
and passport scored 1.000 precision/1.000 recall; SSN scored 0.500/1.000; bank number scored
0.333/1.000. Treat these as wiring checks, not reliable accuracy estimates.

The following configured types do not yet have enough labelled examples for a public
precision/recall number:

- Google, GitHub, Slack, and Stripe keys; JWTs; private keys; credential-bearing URLs and
  connection strings;
- credit cards and IP addresses;
- Indian UPI, IFSC, pincode, and driving licence;
- all six UK identifier types; and
- all six medical types.

They are implemented, but “not measured” does not mean either 0% or 100% accuracy.

### Images

On the same 19-image corpus, PaddleOCR found 31 of 31 hand-checked text targets: a 100% OCR
text-extraction recall proxy. That is not a complete PII precision/recall score. Image
redaction can still miss text that OCR reads incorrectly, and the quality gate refuses to
write an output when a scan is too unclear.

## Privacy and safety design

- Redaction blocks outbound sockets. Only `download-models` enables network access, in a
  separate process.
- Models and optional identity data live under `~/.holdmydata/`.
- Output is written to a new file by default. Existing output is not overwritten unless
  `--force` is passed.
- If OCR or a detector fails, the command refuses the file instead of returning an
  apparently safe copy.
- Images use solid boxes, not reversible blur.

## Known limits

- No direct PDF ingestion or graphical app in v0.1.1.
- Kannada OCR is not supported. Hindi and Tamil are optional.
- Blurry, dark, rotated, or unusual scans can still lose text during OCR.
- US, UK, and medical coverage needs larger labelled evaluations.
- Address and date-of-birth detection currently over-redact.
- `--who mine` is not supported for images; images always redact everyone’s detected data.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and
[SECURITY.md](SECURITY.md). Do not put real personal information, credentials, or private
documents in an issue, test, or pull request.

## License

Apache License 2.0. See [LICENSE](LICENSE).
