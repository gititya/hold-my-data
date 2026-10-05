"""Builds the analyzer + anonymizer from config, and runs text through them.

Fail closed: if any recogniser raises, we do not return partially-redacted text. Partially
redacted output looks like success and is the worst possible failure mode for this tool.
"""

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

import re

from . import logging_safe, operators
from .operators import ConsistentReplace, StableHash
from .recognizers import address_recognizer, gliner_recognizer, regex_recognizers

# Markdown converters (marker included) sometimes embed page images inline as base64 data
# URIs. There is no PII inside image bytes, but that text still reaches the analyzer -- and
# a base64 blob has no whitespace, so GLiNER's windower and the address model's line-chunker
# both choke on it: confirmed directly, one real 23KB doc with 3 embedded images took the
# address model past its token limit (a separate crash, fixed in address_recognizer.py) and
# then ran GLiNER's own windowing for 5+ minutes with no end in sight, because base64's `+`,
# `/`, `=` punctuation carves the blob into thousands of tiny windows. Masked out, not
# deleted -- same length, same character positions, so span offsets from `analyze()` stay
# valid against the real (unmasked) text that `anonymize()` writes back out.
_BASE64_BLOB = re.compile(r"(?<=base64,)[A-Za-z0-9+/=]{100,}")


# Found 2026-08-21 re-running the real-world corpus: blank government forms and rental
# boilerplate get PERSON/DOB hits on the FORM FIELD LABELS themselves ("First Name", "Date of
# birth" printed on an empty IDP application form) or a contract's own defined terms ("Owners",
# "Tenants" in a rental agreement), not any real filled-in data. Not a leak either way -- a
# blank form has no PII to expose -- but it over-redacts harmless structural text. GLiNER is a
# zero-shot model with no "not filled in" concept; an exact-match stoplist of common
# bureaucratic/legal phrases is a small, low-risk filter, since a real person's name or address
# is never going to be the literal string "First Name" or "Tenants".
_FORM_LABEL_STOPLIST = {
    "first name", "middle name", "last name", "surname", "full name", "applicant",
    "father's name", "husband's name", "mother's name", "guardian's name",
    "father's or husband's name", "mother's or guardian's name",
    "date of birth", "place of birth", "nationality", "sex", "gender", "occupation",
    "signature", "signatory", "witness", "declarant",
    "owner", "owners", "tenant", "tenants", "parties", "lessor", "lessee",
    "permanent address", "present address", "residential address", "correspondence address",
}


def _looks_like_form_label(entity_type: str, value: str) -> bool:
    if entity_type not in {"PERSON", "DOB", "ADDRESS", "CUSTOMER_ID"}:
        return False
    return re.sub(r"\s+", " ", value.strip().lower()) in _FORM_LABEL_STOPLIST


def _mask_binary_blobs(text: str) -> str:
    return _BASE64_BLOB.sub(lambda m: "0" * len(m.group()), text)


class RedactionFailed(RuntimeError):
    """Detection failed. Nothing is emitted -- see SPEC 5."""


def _nlp_engine():
    """Tokenisation only.

    spaCy's own NER is disabled: Presidio needs an NLP engine to tokenise, it does not need
    spaCy guessing at entities. GLiNER does that job, driven by our taxonomy.
    """
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            "ner_model_configuration": {"labels_to_ignore": ["ALL"]},
        }
    )
    return provider.create_engine()


def _add_predefined(registry, config, entity_names) -> list:
    """Pull in Presidio's own recognisers for entities that ask for them.

    Presidio ships validated recognisers for India (IN_AADHAAR, IN_PAN, IN_GSTIN,
    IN_VOTER, IN_PASSPORT, IN_VEHICLE_REGISTRATION) and the US (US_SSN, US_ITIN, ...),
    several with real checksum validation. Rewriting those by hand would be strictly worse.

    Entity names must match Presidio's exactly. A name that matches nothing is an error, not
    a warning -- a silently absent recogniser is indistinguishable from clean input.
    """
    wanted = [
        name
        for name in entity_names
        if config.entities[name].uses_presidio
    ]
    if not wanted:
        return []

    source = RecognizerRegistry()
    source.load_predefined_recognizers(languages=["en"])

    # Presidio loads US and global recognisers by default but leaves the country-specific
    # ones off, India included. They have to be instantiated explicitly.
    import presidio_analyzer.predefined_recognizers as predefined

    for class_name in (
        # India
        "InAadhaarRecognizer", "InPanRecognizer", "InGstinRecognizer",
        "InVoterRecognizer", "InPassportRecognizer", "InVehicleRegistrationRecognizer",
        # UK
        "UkNinoRecognizer", "NhsRecognizer", "UkPassportRecognizer",
        "UkPostcodeRecognizer", "UkVehicleRegistrationRecognizer",
    ):
        source.add_recognizer(getattr(predefined, class_name)())

    # Presidio 2.2.363 introduced this recognizer, but 2.2.363 also introduced a
    # PyYAML>=6.0.3 requirement that conflicts with PaddleOCR/PaddleX 3.7.0's exact
    # PyYAML==6.0.2 requirement. Keep the same documented DVLA-format recognizer locally so
    # the install remains consistent without silently dropping UK driving licences.
    if hasattr(predefined, "UkDrivingLicenceRecognizer"):
        source.add_recognizer(predefined.UkDrivingLicenceRecognizer())
    else:
        from presidio_analyzer import Pattern, PatternRecognizer

        source.add_recognizer(
            PatternRecognizer(
                supported_entity="UK_DRIVING_LICENCE",
                supported_language="en",
                patterns=[
                    Pattern(
                        "UK Driving Licence",
                        r"\b[A-Z9]{5}[0-9](?:0[1-9]|1[0-2]|5[1-9]|6[0-2])"
                        r"(?:0[1-9]|[12][0-9]|3[01])[0-9][A-Z9]{2}[A-Z0-9][A-Z]{2}\b",
                        0.5,
                    )
                ],
                context=[
                    "driving licence", "driving license", "driver's licence",
                    "driver's license", "dvla", "dl number", "licence number",
                    "license number",
                ],
            )
        )

    # The medical recogniser is a separate ~500MB HuggingFace model. Only construct it when
    # a context actually asks for medical entities -- nobody should pay that cost to redact
    # an API key.
    if any(name.startswith("MEDICAL_") and name != "MEDICAL_LICENSE" for name in wanted):
        source.add_recognizer(predefined.MedicalNERRecognizer())

    available = set()
    for recognizer in source.recognizers:
        supported = set(recognizer.supported_entities) & set(wanted)
        if supported:
            registry.add_recognizer(recognizer)
            available |= supported

    missing = sorted(set(wanted) - available)
    if missing:
        raise RedactionFailed(
            f"no Presidio recogniser exists for {missing}. Either the name is wrong or that "
            "entity needs `detect_with: regex` and a pattern of your own. A recogniser that "
            "silently never fires looks exactly like clean input."
        )
    logging_safe.debug(f"presidio predefined recognisers active for {sorted(available)}")
    return sorted(available)


class Redactor:
    def __init__(self, config, context="default", use_gliner=True, entities: list = None):
        """`entities`, if given, narrows which entities this Redactor is built for at all --
        e.g. entities=["ADDRESS"] against a context that also includes PERSON means the
        GLiNER recognizer is never even constructed, so its model never loads.

        This is the ONLY point that actually controls model loading. Presidio's own
        `EntityRecognizer.__init__` calls `self.load()` itself (verified directly,
        2026-08-21: `presidio_analyzer/entity_recognizer.py`), so every recognizer's model
        loads the moment it's constructed here, regardless of any later per-call filter
        passed to `.analyze()`. A per-call `entities` filter on `.analyze()` narrows *what
        gets searched for*, not *what gets loaded* -- if you want a call to skip loading a
        model entirely, it has to not be in the entity list given here, at construction time.
        """
        self.config = config
        self.context = config.context(context)
        self.use_gliner = use_gliner
        self._active_entities = (
            list(self.context.entities)
            if entities is None
            else [e for e in entities if e in self.context.entities]
        )

        registry = RecognizerRegistry()
        for recognizer in regex_recognizers.build(config, self._active_entities):
            registry.add_recognizer(recognizer)

        self._presidio_entities = _add_predefined(registry, config, self._active_entities)

        self._gliner = None
        if use_gliner:
            gl = gliner_recognizer.GlinerRecognizer(
                config,
                self._active_entities,
                threshold=self.context.threshold,
            )
            if gl.supported_entities:
                registry.add_recognizer(gl)
                self._gliner = gl

        self._address = address_recognizer.AddressRecognizer(
            config, self._active_entities, threshold=self.context.threshold
        )
        if self._address.supported_entities:
            registry.add_recognizer(self._address)

        self.analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=_nlp_engine(),
            supported_languages=["en"],
            default_score_threshold=self.context.threshold,
        )

        self.anonymizer = AnonymizerEngine()
        self.anonymizer.add_anonymizer(ConsistentReplace)
        self.anonymizer.add_anonymizer(StableHash)

    def close(self) -> None:
        """Drop references to loaded model weights (GLiNER, the address transformers
        pipeline) so they become garbage as soon as nothing else holds them.

        Only matters for a long-running host process that keeps a `Redactor` around across
        many calls -- a normal one-shot CLI invocation exits right after this call would
        return, and the OS reclaims everything anyway. Diagnosed 2026-08-21 after a
        long-running eval script (not this class -- see images.py's own cache) accumulated
        every OCR language model it ever touched across 13 images in one process. This is
        the equivalent release point for the two model-backed recognizers this class owns.
        Does not touch `holdmydata.images`'s separate OCR engine cache -- call
        `images.reset_ocr_engines()` for that.
        """
        if self._gliner is not None:
            self._gliner._model = None
        self._address._pipeline = None

    # -- detection -------------------------------------------------------------

    def _prefer_specific(self, results: list) -> list:
        """Drop model spans that overlap a deterministic hit.

        Without this, a broad model guess can swallow a checksum-validated IN_PAN -- the text
        is still redacted, but it comes back labelled as the vaguer thing. That makes the
        output less readable and, worse, scores as a miss plus a false positive during eval.
        Deterministic detectors (regex, Presidio's validated recognisers) win every overlap;
        the model layer is for what they cannot see.
        """
        def deterministic(r) -> bool:
            entity = self.config.entities.get(r.entity_type)
            return bool(entity and (entity.uses_regex or entity.uses_presidio))

        # per-entity floors first -- a global threshold cannot separate detectors whose
        # scores are not calibrated against each other
        # A recogniser can emit entity types we never declared -- Presidio's medical NER
        # returns DATE, for one. We cannot name or replace what is not in the taxonomy, so
        # drop it rather than crash mid-document.
        known = [r for r in results if r.entity_type in self.config.entities]
        for r in results:
            if r.entity_type not in self.config.entities:
                logging_safe.debug(
                    "ignoring undeclared entity type %s from a recogniser", r.entity_type
                )

        # the epsilon is not paranoia: Presidio returns 0.44999999999999996 for a score it
        # documents as 0.45, so an exact >= comparison silently drops real detections
        results = [
            r
            for r in known
            if r.score >= (self.config.entities[r.entity_type].min_score or 0.0) - 1e-9
        ]

        hard = [r for r in results if deterministic(r)]
        if not hard:
            return results

        kept = list(hard)
        for r in results:
            if deterministic(r):
                continue
            if any(r.start < h.end and h.start < r.end for h in hard):
                logging_safe.debug(
                    "dropping %s [%d:%d] — overlaps a deterministic hit",
                    r.entity_type, r.start, r.end,
                )
                continue
            kept.append(r)
        return kept

    def analyze(self, text: str, entities: list = None, threshold: float = None) -> list:
        """`entities`, if given, narrows this ONE call to a subset of this Redactor's active
        entities (e.g. ["ADDRESS"]) -- Presidio's own per-call filter. This does NOT skip
        loading a model that this Redactor was constructed to support -- every recognizer's
        model already loaded when this object was built (see `Redactor.__init__`'s own
        `entities` parameter, which is the actual point of control for that). This parameter
        only limits what gets searched for in this specific call, useful when reusing one
        already-built Redactor for several narrower asks. Defaults to everything this
        Redactor supports, same as before this parameter existed.

        `threshold`, if given, replaces the context's cut-off for this one call (used to
        rescan a PDF line at a lower bar). It reaches the name model too.
        """
        cutoff = self.context.threshold if threshold is None else threshold
        saved = self._gliner._threshold if self._gliner is not None else None
        if self._gliner is not None:
            self._gliner._threshold = cutoff
        wanted = self._active_entities if entities is None else [e for e in entities if e in self._active_entities]
        try:
            results = self._prefer_specific(
                self.analyzer.analyze(
                    text=_mask_binary_blobs(text),
                    language="en",
                    entities=wanted,
                    score_threshold=cutoff,
                )
            )
            return [
                r for r in results
                if not _looks_like_form_label(r.entity_type, text[r.start : r.end])
            ]
        except Exception as exc:
            raise RedactionFailed(
                f"a recogniser failed ({type(exc).__name__}: {exc}). Nothing was emitted. "
                "Partial redaction is not an acceptable degraded mode."
            ) from exc
        finally:
            if self._gliner is not None:
                self._gliner._threshold = saved

    # -- anonymisation ---------------------------------------------------------

    def _operators(self) -> dict:
        strategy = self.context.strategy
        operators = {}

        # PERSON is always included: hand-typed "Hide More" words are labelled PERSON even
        # when the name category is off.
        for name in dict.fromkeys([*self._active_entities, "PERSON"]):
            entity = self.config.entities[name]
            if strategy == "consistent":
                operators[name] = OperatorConfig(
                    "consistent", {"replacement": entity.replacement}
                )
            elif strategy == "label":
                operators[name] = OperatorConfig(
                    "replace", {"new_value": entity.replacement or f"<{name}>"}
                )
            elif strategy == "mask":
                operators[name] = OperatorConfig(
                    "mask",
                    {"masking_char": "*", "chars_to_mask": 100, "from_end": False},
                )
            elif strategy == "hash":
                operators[name] = OperatorConfig("stablehash", {})
        return operators

    def redact(self, text: str, entities: list = None) -> str:
        return self.anonymize(text, self.analyze(text, entities=entities))

    def anonymize(self, text: str, results: list) -> str:
        """Turn already-found spans into redacted text.

        Split out from `redact` so a caller can filter `results` first -- e.g. the "mine
        only" mode in `identity.py` drops spans that do not match the user's own identifiers
        before anonymising the rest.
        """
        operators.reset_run()

        counts = {}
        for r in results:
            counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
        logging_safe.log_summary(counts)

        if not results:
            return text

        # Prime the mapping in reading order. Presidio anonymises back-to-front, so without
        # this the numbering would run backwards through the document.
        if self.context.strategy == "consistent":
            for r in sorted(results, key=lambda r: r.start):
                operators.assign(
                    r.entity_type,
                    text[r.start:r.end],
                    self.config.entities[r.entity_type].replacement,
                )

        try:
            out = self.anonymizer.anonymize(
                text=text, analyzer_results=results, operators=self._operators()
            )
        except Exception as exc:
            raise RedactionFailed(
                f"anonymisation failed ({type(exc).__name__}: {exc}). Nothing was emitted."
            ) from exc
        finally:
            # the mapping is a re-identification key -- do not let it outlive the run
            operators.reset_run()
        return out.text


def term_hits(text: str, terms) -> list:
    """Spans for words the user asked to hide by hand (the app's "Hide More"). Case-insensitive,
    and a multi-word term matches across line breaks. Labelled PERSON: these are mostly names
    the detector missed.
    """
    import re

    from presidio_analyzer import RecognizerResult

    hits = []
    for term in terms or ():
        words = str(term).split()
        if not words:
            continue
        pattern = re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE)
        hits += [RecognizerResult("PERSON", m.start(), m.end(), 1.0) for m in pattern.finditer(text)]
    return hits


def count_spans(results) -> dict:
    """{entity_type: how many places}, counting a span two detectors both found once.

    PHONE and IN_PHONE both match the same number; the redacted text only changes once, so the
    receipt must say 1, not 2. Overlapping hits form one place, named by the highest score.
    """
    counts, current_end, best = {}, -1, None
    for r in sorted(results, key=lambda r: (r.start, -r.end)):
        if r.start < current_end:
            current_end = max(current_end, r.end)
            if r.score > best.score:
                counts[best.entity_type] -= 1
                counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
                best = r
            continue
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
        current_end, best = r.end, r
    return {k: v for k, v in counts.items() if v}
