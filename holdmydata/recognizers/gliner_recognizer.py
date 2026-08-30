"""GLiNER wrapped as a Presidio EntityRecognizer.

The point of zero-shot: the label set is passed at call time as plain English, taken verbatim
from each entity's `description` in taxonomy.yaml. Adding a category is a config edit, not a
retrain. That is the whole reason this project needs no training data.

The model is loaded from a local path. Downloading is a separate, deliberate act -- see
`offline.py` and `scripts/fetch_model.py`.
"""

import os
import re
import warnings

from presidio_analyzer import EntityRecognizer, RecognizerResult

from .. import logging_safe

DEFAULT_MODEL = "hugmyface0907/gliner-indian-names-v1"
BASE_MODEL = "urchade/gliner_multi_pii-v1"
MODEL_DIR_ENV = "HOLDMYDATA_MODEL_DIR"

# hold-my-data/gliner-indian-names-v1 (renamed 2026-08-19 from gliner-person-v1 -- clearer name,
# this is specifically Indian-name-tuned, not a general person detector) is BASE_MODEL
# continued-trained on PERSON only (Luna's
# 228K synthetic name-sentences, 227,978 after holding out eval/labelled/luna_person_sample.jsonl).
# Verified on 400 genuinely real, unseen Wikipedia biography sentences (not the synthetic
# templates it trained on): recall 18.8% -> 99.3%, precision 63.6% -> 85.9%. DOB/CUSTOMER_ID
# detection (never fine-tuned) confirmed unaffected. 2026-08-18.

# GLiNER's per-label confidence is NOT independent of what else is in the same query --
# verified directly: a real Indian full name scores 0.22 as PERSON asked alone among 3 labels,
# 0.62 asked among 5. Trimming ADDRESS/ORGANISATION out of the active taxonomy (address now
# has its own dedicated model, org was never wanted) silently cut PERSON recall in half via
# this side channel. These are sent to every query purely to hold the calibration steady --
# not in _label_to_entity, so their hits are dropped automatically, never surfaced.
CALIBRATION_LABELS = [
    "a physical or postal address",
    "the name of a company or organisation",
]

# The model's max_len is 384 subword tokens and GLiNER enforces it with `tokens = tokens[:max_len]`
# — a silent truncation carrying only a UserWarning. Hand it a whole document and everything past
# the first 384 tokens is never looked at: under-redaction that reports success, which is the
# failure mode this project cares most about.
#
# So window the text and offset the hits back.
#
# Measure in GLiNER's OWN unit, which is neither words nor subwords: `prepare_inputs` builds
# `prompt + list(text)` where text has been through GLiNER's WordsSplitter, and punctuation is
# its own token. A markdown table row `| 1,23,456.78 |` is 3 whitespace words, 16 subword
# tokens, and 14 GLiNER tokens. Measuring with the transformer's subword tokenizer under-reads
# table-heavy text by 2x — windows verified at 320 subwords came out at 693 GLiNER tokens.
SAFETY_MARGIN = 32  # slack for _extra_prompt_tokens, which we do not model
OVERLAP_TOKENS = 48  # >= the longest entity we expect, so none is cut in half


def _windows(text: str, atoms: list, budget: int) -> list:
    """Split into overlapping (chunk, char_offset) windows of at most `budget` atoms.

    `atoms` is GLiNER's own (word, start, end) tokenisation, so the count is exact rather
    than estimated. Window edges fall on atom boundaries, which means re-splitting a chunk
    yields exactly the atoms it was built from.

    Every atom lands in at least one window, and the overlap means an entity sitting on a
    boundary is still seen whole by one of them.
    """
    if not atoms:
        return []

    out, i, n = [], 0, len(atoms)
    while i < n:
        j = min(i + budget, n)
        out.append((text[atoms[i][1] : atoms[j - 1][2]], atoms[i][1]))
        if j >= n:
            break
        i = j - OVERLAP_TOKENS  # budget > OVERLAP_TOKENS, so this always advances
    return out


class GlinerRecognizer(EntityRecognizer):
    """Maps GLiNER's plain-English labels back onto taxonomy entity names."""

    def __init__(self, config, entity_names, threshold=0.3, model_name=None):
        self._config = config
        self._threshold = threshold
        self._model_name = (
            model_name or os.environ.get("HOLDMYDATA_GLINER_MODEL") or DEFAULT_MODEL
        )
        self._model = None

        # description -> ENTITY_NAME. GLiNER answers in the label we handed it.
        self._label_to_entity = {}
        for name in entity_names:
            entity = config.entities.get(name)
            if entity and entity.uses_gliner:
                self._label_to_entity[entity.description] = name

        super().__init__(
            supported_entities=list(self._label_to_entity.values()),
            name="gliner",
            supported_language="en",
        )

    @property
    def labels(self) -> list:
        return list(self._label_to_entity)

    def load(self) -> None:
        if self._model is not None or not self._label_to_entity:
            return

        from gliner import GLiNER
        import torch

        # Uncapped, PyTorch spins up one intra-op thread per CPU core, and each thread carries
        # its own matmul workspace buffers -- that multiplies, not just adds, on machines with
        # many cores. HOLDMYDATA_TORCH_THREADS lets a low-memory machine turn this down further.
        torch.set_num_threads(int(os.environ.get("HOLDMYDATA_TORCH_THREADS", "4")))

        local_dir = os.environ.get(MODEL_DIR_ENV)
        source = os.path.join(local_dir, self._model_name.replace("/", "__")) if local_dir else self._model_name

        logging_safe.debug(f"loading gliner model from {source}")
        self._model = GLiNER.from_pretrained(source, local_files_only=local_dir is not None)
        self._model.eval()

    def analyze(self, text, entities, nlp_artifacts=None) -> list:
        if not self._label_to_entity:
            return []

        wanted = set(entities) if entities else set(self._label_to_entity.values())
        labels = [
            label
            for label, name in self._label_to_entity.items()
            if name in wanted
        ]
        if not labels:
            # Matches address_recognizer.py's pattern: check whether THIS CALL actually
            # wants anything GLiNER handles before paying any further cost. Note this cannot
            # prevent the model's initial load if GLiNER was already constructed for a
            # PERSON-including context -- Presidio's own EntityRecognizer.__init__ calls
            # self.load() eagerly, before any analyze() call ever runs (verified directly,
            # 2026-08-21). The real point of control for "never load GLiNER at all" is
            # Redactor's own `entities` constructor parameter (engine.py), which decides
            # whether this recognizer is even built. This check just avoids doing anything
            # further on a call that plainly doesn't need it.
            return []
        if self._model is None:
            self.load()
        labels = labels + CALIBRATION_LABELS

        # prepare_inputs prepends [ent_token, label] per label plus a separator.
        processor = self._model.data_processor
        budget = self._model.config.max_len - (2 * len(labels) + 1) - SAFETY_MARGIN

        windows = _windows(text, list(processor.words_splitter(text)), budget)
        if not windows:
            return []

        # Fail closed. GLiNER truncates over-long input with a UserWarning and returns a
        # normal-looking result, so a warning here means we silently skipped part of the
        # document. Promote it to an exception; engine turns that into RedactionFailed.
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message=".*has been truncated.*")
            try:
                batches = self._model.inference(
                    [chunk for chunk, _ in windows], labels, threshold=self._threshold
                )
            except UserWarning as exc:
                raise RuntimeError(
                    f"gliner truncated a window — part of the input was never scanned: {exc}"
                ) from exc

        # Overlapping windows report the same entity twice. Key on the span and keep the
        # higher score.
        best = {}
        for (_, offset), hits in zip(windows, batches):
            for hit in hits:
                entity_name = self._label_to_entity.get(hit["label"])
                if not entity_name:
                    continue
                span = (entity_name, hit["start"] + offset, hit["end"] + offset)
                score = float(hit["score"])
                if score > best.get(span, 0.0):
                    best[span] = score

        results = []
        for (entity_name, start, end), score in sorted(best.items(), key=lambda kv: kv[0][1]):
            logging_safe.log_finding(entity_name, start, end, score)
            results.append(
                RecognizerResult(
                    entity_type=entity_name,
                    start=start,
                    end=end,
                    score=score,
                    analysis_explanation=None,
                )
            )
        return results
