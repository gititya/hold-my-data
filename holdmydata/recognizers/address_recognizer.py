"""Indian address detection, wrapped as a Presidio EntityRecognizer.

GLiNER scored 0% recall on real Indian addresses -- it was never going to catch these.
shiprocket-ai/open-indicbert-indian-address-ner is a model actually trained for this: it
tags 11 address components (house, road, locality, city, state, pincode, ...) rather than
guessing from general context. Validated 80% precision / 100% recall on real addresses,
see evidence/eval-full-2026-08-18.md.

Its 11 component labels are merged into a single ADDRESS span here -- taxonomy.yaml has one
ADDRESS entity, not eleven, and a redacted address should read as one [ADDRESS] tag, not a
run of six separate ones.
"""

import os
import re

from presidio_analyzer import EntityRecognizer, RecognizerResult

from .. import logging_safe

# Some printed Indian addresses glue the pincode straight onto a hyphen with no space
# ("Tamilnadu -600041") -- verified directly that this exact pattern makes the model tag
# nothing at all for the pincode, while the same address with a space ("- 600041" or just
# " 600041") tags it at 1.0 confidence. This was the root cause of the pincode-suffix half
# of the ADDRESS boundary-trim gap logged in PII_COVERAGE.md 2026-08-19f. Swapping the
# hyphen for a space is a same-length substitution (one char for one char), so every
# downstream offset computed against the normalised text is still a valid offset into the
# original text -- no need to touch anonymize()'s view of the string.
_HYPHEN_PINCODE = re.compile(r"-(?=\d{5,6}\b)")


def _normalize(text: str) -> str:
    return _HYPHEN_PINCODE.sub(" ", text)

DEFAULT_MODEL = "shiprocket-ai/open-indicbert-indian-address-ner"
MODEL_DIR_ENV = "HOLDMYDATA_MODEL_DIR"

# Component spans this close together (a comma, a space, ", ") are one address, not two.
MERGE_GAP = 3

# The model was validated (evidence/eval-full-2026-08-18.md) on short, address-only text --
# every logged span there is 33-110 characters. Fed a whole OCR'd page instead (2000+ chars of
# legal boilerplate around the address), it drops from 99%+ confidence to below-threshold noise
# -- confirmed 2026-08-19f by running the same model on the same address text standalone
# (0.98-0.99 confidence) vs. embedded in the full page (0.36-0.39, all missed). Chunking the
# text back down to roughly what it was validated on fixes this. One line can split an address
# across a line break (e.g. "...Chennai,\nTamilnadu -600041,"), so chunks overlap by one line.
CHUNK_TARGET_CHARS = 200
CHUNK_OVERLAP_LINES = 1

# The line-based chunking below always keeps at least one full line, even past
# CHUNK_TARGET_CHARS, on the assumption that a real line of prose is never absurdly long.
# That assumption broke on a real document: a base64-embedded image is one "line" with no
# whitespace at all, thousands of characters long, and reached the model whole -- 5067
# tokens against its 512-position limit, a hard crash. No real address is ever this long, so
# a line past this cap is force-split on character count, at its own already-correct offset,
# instead of ever being handed to the model whole.
MAX_LINE_CHARS = 4 * CHUNK_TARGET_CHARS


def _chunk(text: str) -> list:
    """Split text into overlapping (chunk_text, offset_in_original) windows, by line."""
    lines = text.split("\n")
    line_starts = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line) + 1

    chunks = []
    i = 0
    while i < len(lines):
        if len(lines[i]) > MAX_LINE_CHARS:
            start = line_starts[i]
            for k in range(0, len(lines[i]), MAX_LINE_CHARS):
                chunks.append((lines[i][k : k + MAX_LINE_CHARS], start + k))
            i += 1
            continue

        j = i
        length = 0
        while j < len(lines) and len(lines[j]) <= MAX_LINE_CHARS and (length == 0 or length < CHUNK_TARGET_CHARS):
            length += len(lines[j]) + 1
            j += 1
        chunk_text = "\n".join(lines[i:j])
        chunks.append((chunk_text, line_starts[i]))
        if j >= len(lines):
            break
        i = max(i + 1, j - CHUNK_OVERLAP_LINES)
    return chunks

# This model has no "not an address" concept -- fed plain text with no address in it, it
# still forces the whole thing into some component label at 99%+ confidence (verified: a
# random sentence came back tagged whole-string "building_name"). A real address almost
# always carries a pincode, or a city+state pair; a lone building_name/road/locality guess
# with neither is exactly the failure mode observed, so a merged group is only trusted if it
# has one of these anchors.
ANCHOR_LABELS = {"pincode"}
ANCHOR_PAIR = {"city", "state"}

# A bare "pincode" tag on its own text is not enough on its own -- found 2026-08-21 re-running
# the full text eval: the model happily tags an unrelated 4-digit fragment of a bank account
# number, or a 9-digit tax figure, as "pincode" at 0.5-0.99 confidence, and the bare
# ANCHOR_LABELS check above trusted it. A real Indian pincode is exactly 6 digits, first digit
# 1-9 (no real PIN code starts with 0) -- validating the matched text's actual shape, not just
# its label, rejects "4551" and "1863023.00" while still accepting "600041".
_PINCODE_SHAPE = re.compile(r"[1-9]\d{5}")


def _valid_pincode(text: str) -> bool:
    return bool(_PINCODE_SHAPE.fullmatch(re.sub(r"\D", "", text)))


# A third, real-world case found 2026-08-20: a genuine address (building/road/locality/city
# all present, 0.85-0.99 confidence each) with no separate "state" word and a pincode OCR
# garbled beyond recognition ("56oo24" -- OCR reads a zero as a letter O) merges its digits
# straight into the "city" label instead of a separate "pincode" one, so it clears neither
# anchor and the whole address leaked, unredacted, in a real rental-agreement photo. A single
# mis-tagged sentence (the false-positive case the two anchors above exist to catch) only
# ever produces ONE component label; a real address's components corroborate each other
# across several *different* labels. Requiring 3+ distinct labels is a third, independent
# anchor that catches this case without weakening the other two.
#
# Re-running the full text eval 2026-08-21 found this anchor is, on its own, still too loose:
# a Wikipedia biography sentence ("... Indian actress who mainly works in Telugu and Hindi
# television.") drew building_name/house_details/road/landmarks tags and cleared 3 distinct
# labels with no real address present. The real leak's labels included at least one specific
# locality-level tag (road/locality/sub_locality/city/state); the biography's only "specific"
# tag was a nonsense one-word fragment. Requiring at least one specific-location label,
# alongside 3+ total, keeps the real-leak fix while narrowing this new false-positive path.
MIN_DISTINCT_LABELS = 3
SPECIFIC_LOCATION_LABELS = {"road", "locality", "sub_locality", "city", "state"}

# A specific-location tag still needs its OWN confidence to be meaningfully above the general
# per-token floor (self._threshold, default 0.3) to count as corroboration -- found 2026-08-21:
# a biography sentence produced a "road" tag at 0.397 confidence on "ember" (a stray fragment
# of the word "December"), which is real-tagged noise, not a real road name. The two genuine
# leak cases this anchor was built for score their specific-location tags at 0.96-0.99; a
# meaningfully higher floor here separates confident, real location tags from marginal noise.
MIN_SPECIFIC_LABEL_SCORE = 0.5


def _merge(spans: list, text: str) -> list:
    """Merge overlapping/near-adjacent (start, end, score, label) spans into contiguous ranges."""
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s[0], s[1]))
    # Keep each group's own constituent (start, end, label) triples -- validating "is this
    # pincode-shaped" needs the specific pincode-labeled sub-span's own text, not the whole
    # merged group's span (which can include other numbers -- a house number, another
    # component -- that would corrupt a naive "strip all non-digits from the whole group" check).
    merged = [[spans[0][0], spans[0][1], spans[0][2], {spans[0][3]}, [spans[0]]]]
    for s in spans[1:]:
        start, end, score, label = s
        last = merged[-1]
        if start <= last[1] + MERGE_GAP:
            last[1] = max(last[1], end)
            last[2] = max(last[2], score)
            last[3].add(label)
            last[4].append(s)
        else:
            merged.append([start, end, score, {label}, [s]])

    out = []
    for start, end, score, labels, members in merged:
        has_pincode = "pincode" in labels and any(
            m[3] == "pincode" and _valid_pincode(text[m[0] : m[1]]) for m in members
        )
        has_pair = ANCHOR_PAIR <= labels
        confident_specific_labels = {
            m[3] for m in members
            if m[3] in SPECIFIC_LOCATION_LABELS and m[2] >= MIN_SPECIFIC_LABEL_SCORE
        }
        has_corroboration = len(labels) >= MIN_DISTINCT_LABELS and confident_specific_labels
        if has_pincode or has_pair or has_corroboration:
            out.append((start, end, score))
    return out


class AddressRecognizer(EntityRecognizer):
    """Wraps the shiprocket-ai Indian address NER model for the ADDRESS entity."""

    def __init__(self, config, entity_names, threshold=0.3, model_name=None):
        self._threshold = threshold
        self._model_name = (
            model_name or os.environ.get("HOLDMYDATA_ADDRESS_MODEL") or DEFAULT_MODEL
        )
        self._pipeline = None
        self._active = any(
            config.entities[name].uses_address_model
            for name in entity_names
            if name in config.entities
        )

        super().__init__(
            supported_entities=["ADDRESS"] if self._active else [],
            name="address_model",
            supported_language="en",
        )

    def load(self) -> None:
        if self._pipeline is not None or not self._active:
            return

        from transformers import pipeline
        import torch

        # See gliner_recognizer.load() for why this matters: uncapped thread count multiplies
        # per-thread matmul workspace memory on many-core machines. Harmless if gliner_recognizer
        # already set it in this process -- set_num_threads is idempotent to call again.
        torch.set_num_threads(int(os.environ.get("HOLDMYDATA_TORCH_THREADS", "4")))

        local_dir = os.environ.get(MODEL_DIR_ENV)
        source = (
            os.path.join(local_dir, self._model_name.replace("/", "__"))
            if local_dir
            else self._model_name
        )

        logging_safe.debug(f"loading address model from {source}")
        self._pipeline = pipeline(
            "token-classification",
            model=source,
            aggregation_strategy="simple",
        )

    def analyze(self, text, entities, nlp_artifacts=None) -> list:
        if not self._active or (entities and "ADDRESS" not in entities):
            return []
        if self._pipeline is None:
            self.load()

        raw = []
        for chunk_text, offset in _chunk(_normalize(text)):
            if not chunk_text.strip():
                continue
            raw.extend(
                (offset + e["start"], offset + e["end"], float(e["score"]), e["entity_group"])
                for e in self._pipeline(chunk_text)
                if e["score"] >= self._threshold
            )

        results = []
        for start, end, score in _merge(raw, text):
            logging_safe.log_finding("ADDRESS", start, end, score)
            results.append(
                RecognizerResult(
                    entity_type="ADDRESS",
                    start=start,
                    end=end,
                    score=score,
                    analysis_explanation=None,
                )
            )
        return results
