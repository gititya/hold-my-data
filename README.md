# Hold My Data

I'm a little fastidious about sharing images and text files that contain my personal information. Tools such as [Presidio](https://github.com/data-privacy-stack/presidio) had trouble finding names—Indian names, to be precise—so I built a personal redaction tool. Of course, it had to run locally.

It uses:

1. Patterns and checksums for fixed formats such as email addresses, PAN, Aadhaar, GSTIN, and API keys.
2. Presidio's built-in recognizers for supported identifiers.
3. A local Indian-address model.
4. A fine-tuned local GLiNER model for names, especially Indian names, and dates of birth.
5. Local PaddleOCR for images.

Text is masked with labels such as `[EMAIL_1]`. Images get a solid box over matching text. Downloading models needs an internet connection; once they are downloaded, the command-line tool blocks outbound network connections while redacting.

> [!WARNING]
> Hold My Data cannot promise that it will find everything. Double-check the result before sharing it, especially when the source is a blurry photo or scan.

## What it detects, and how well

**Precision** means: of everything flagged, how much was correct.

**Recall** means: of everything that should have been flagged, how much was found.

**F1** is a combined score that balances precision and recall.

I chose to intentionally priority recall because the risk of missing my personal data > the tool redacting "too much" data. 

The current text evaluation contains 1,051 labelled examples. Overall precision is **70.1%**, recall is **99.0%**, and F1 is **82.1%**. The evaluation includes real personal documents, so the raw labelled files are not public. The evaluation code is included in this repository, and I will keep updating the results as the stress tests grow.

### Measured text results

| Entity | Precision | Recall | Labelled items |
|---|---:|---:|---:|
| Person name | 91.2% | 99.5% | 910 |
| Indian PAN | 100% | 100% | 19 |
| Indian GSTIN | 100% | 100% | 4 |
| Indian Aadhaar | 100% | 100% | 3 |
| Indian voter ID | 100% | 100% | 3 |
| Indian vehicle registration | 100% | 100% | 4 |
| Indian passport | 62.5% | 100% | 5 |
| Indian phone | 100% | 100% | 2 |
| Email | 100% | 100% | 4 |
| Anthropic, OpenAI, OpenRouter, and ElevenLabs keys combined | 100% | 100% | 11 |
| Address | 14.3% | 100% | 6 |
| Date of birth | 1.5% | 100% | 4 |
| General phone number | 25.0% | 100% | 1 |

Low address and date-of-birth precision means the tool hides too much; it does not mean the measured items leaked. Results based on fewer than about 20 labelled items are early signals, not strong accuracy claims.

### Measured image results

I marked 31 pieces of text across 19 test images. PaddleOCR successfully read all 31. This only measures whether the image reader could see that text—it does **not** prove that every piece of personal information in an image will be found and redacted. If the scan is too unclear or a detector fails, Hold My Data refuses to create an output instead of returning a file that only looks safe.

## Setup

Requirements: macOS, Git, and about 1.6 GB of free disk space for the program. Python is installed inside Hold My Data's private environment. Running every model may use about 4 GB of memory; close other heavy apps if your Mac has 8 GB of RAM.

### I know what `brew` means on my Mac

```bash
brew install uv
git clone https://github.com/gititya/hold-my-data.git
cd hold-my-data
uv tool install --python 3.13 .
hold-my-data check
```

You do not need a GitHub account. If `hold-my-data` is not found after installation, run `uv tool update-shell`, close Terminal, and open it again.

### I rely on Codex, Claude Code, or Cursor

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

These are the model families pulled by the current release:

| Purpose | Model | Measured disk use |
|---|---|---:|
| Names and dates of birth | `hugmyface0907/gliner-indian-names-v1` | 1.1 GB |
| Indian addresses | `shiprocket-ai/open-indicbert-indian-address-ner` | 140 MB |
| English image reading | PaddleOCR v6 detection, recognition, orientation, and document models | 177 MB |
| Optional Hindi and Tamil image reading | PaddleOCR language models, added to English | about 100 MB more |

With every English model, expect about **3.0 GB** of total disk use. Adding Hindi and Tamil takes it to about **3.1 GB**.

> [!INFO]
> The first model-backed text run took 16 seconds in the clean test. A clear synthetic image took 13 seconds; the 19 real test images averaged 53.9 seconds each. Difficult scans can take more than 100 seconds.

## Mac app

A small Mac app (Apple Silicon, macOS 15 or newer): drop files, folders or PDFs, choose what to hold, and it saves redacted copies. Close the window and it stays in the menu bar.

1. Download `HoldMyData-0.2.3.dmg` from the [latest release](https://github.com/gititya/hold-my-data/releases/latest).
2. Open it and drag Hold My Data to Applications.
3. The first launch is blocked because the app is not notarised. Open System Settings, Privacy & Security, and click **Open Anyway**.
4. Click **Set Up** (needs internet, about 1.9 GB). After that it runs offline.

## Usage

First, confirm that the install works with fake data. Fake data is used here only so you do not put real personal information into your Terminal history:

```bash
printf 'Email alex@example.com or call +1 415 555 0134' | \
  hold-my-data text --who everyone --entities EMAIL,PHONE --no-gliner
```

Then use it on a real text or Markdown file. The original file stays unchanged:

```bash
hold-my-data doc -i notes.md --who everyone --context india_full
# creates notes.redacted.md
```

For an image, download the image model first and then run:

```bash
hold-my-data image -i photo.png -o photo.redacted.png \
  --who everyone --context india_full
```

Redact a PDF. Each page is read as an image and the result is a new flattened PDF with solid boxes, so it has no selectable text and nothing hidden is left behind. Expect about a minute per page.

```bash
hold-my-data pdf -i lease.pdf -o lease.redacted.pdf --who everyone --context india_full
```

### Redact only my information

This is optional. It saves the details you enter to `~/.holdmydata/identity.yaml` on your Mac and uses them only when you choose `--who mine`.

```bash
hold-my-data setup
hold-my-data doc -i notes.md --who mine --context india_full
```

Normal redaction does not ask for or require your personal details. `--who mine` is not supported for images; images always redact everyone's detected information.

## Use as a Python library

Hold My Data is a local Python library, not a hosted API(yet). Install it into a project from the clone:

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

An integrating product can add its own entity types at runtime with `holdmydata.extend()`. See [INTEGRATION.md](INTEGRATION.md) for the integration contract.

## Privacy and safety design

- Command-line redaction blocks outbound sockets. Only `download-models` enables network access, in a separate process.
- Models and optional identity data live under `~/.holdmydata/`.
- Output is written to a new file by default. An existing output is not overwritten unless `--force` is passed.
- If OCR or a detector fails, the command refuses the file instead of returning an apparently safe copy.
- Images use solid boxes, not reversible blur.

The Python library does not install the command-line tool's network block automatically. Products using the library must provide and test their own network boundary. See [INTEGRATION.md](INTEGRATION.md).

## Known limits

- PDF output is flattened: pages become images and lose selectable text.
- Handwriting and signatures are not read.
- Blurry, dark, rotated, or unusual scans can still lose text during OCR.
- US, UK, and medical coverage needs larger labelled evaluations.
- Address and date-of-birth detection currently over-redacts.
- Image redaction cannot currently target only your own information.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md). Do not put real personal information, credentials, or private documents in an issue, test, or pull request.

## License

Apache License 2.0. See [LICENSE](LICENSE).
