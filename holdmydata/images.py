"""Image redaction: OCR -> the same analyzer -> solid black boxes.

Same config, same rules, same thresholds as text. The only difference is how the text got
here.

PaddleOCR replaces Tesseract as of 2026-08-20 -- see BUILDS.md and
evidence/eval-real-world-2026-08-20.md for the full head-to-head. On the same 19-image
real-world corpus: 100% recall proxy vs. Tesseract's 42%, including several images
Tesseract could never read at any setting (multi-language packs, multiple page-segmentation
modes, all tried). License (Apache-2.0) and offline behaviour (zero network calls under this
project's own guard, confirmed by direct test) verified before adopting, not assumed.

Known limitation (PRD, working notes): recall on images is bounded by OCR quality. Text the
OCR misreads is text the detectors never see. Do not report image recall as comparable to
text recall.

Hindi and Tamil OCR passes added 2026-08-21 (SUPPLEMENTARY_LANGS below) -- Kannada has no
PaddleOCR model in this version and stays unaddressed, Adi's explicit call.
"""

import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from . import atomic_io, logging_safe
from .engine import RedactionFailed

MODEL_DIR_ENV = "HOLDMYDATA_MODEL_DIR"


class OcrEmpty(RedactionFailed):
    """OCR read no usable text at all -- see MIN_OCR_CONFIDENCE / MIN_OCR_WORDS below."""


# Calibrated 2026-08-20 against the full 19-image real-world corpus (not just hand-picked
# hard cases -- see evidence/eval-real-world-2026-08-20.md). Result: 18 of 19 images scored
# 0.74-1.00 mean confidence with 15-282 real words; the one image genuinely unreadable by
# both Tesseract and PaddleOCR (a near-blank phone photo) scored 0.11 confidence / 1 word.
# A wide, clean gap, no ambiguous middle ground. PaddleOCR runs exactly ONE OCR pass per
# image (unlike Tesseract's old "try N candidates, keep the best" design), so the
# multiple-comparisons false-accept regression found and reverted earlier the same session
# (broadening Tesseract's candidate pool let two genuinely bad photos slip past the gate by
# chance) does not apply to this design -- there is no "best of N" step here to bias.
MIN_OCR_CONFIDENCE = 0.5
MIN_OCR_WORDS = 5

# Bar for the per-line name rescan on PDF pages. The page-wide pass uses the context bar
# (0.4); a name that scored 0.34 in a page and 0.98 alone on its line is what this recovers.
LINE_RESCAN_THRESHOLD = 0.31  # not 0.3: a bare pincode scores 0.3, and a pincode inside an address
# would then outrank the address (deterministic hits win overlaps) and erase it.

# Same garbage filter used on the text path's OCR-adjacent cleanup and the old Tesseract
# pipeline: a token below MIN_OCR_CONFIDENCE that also LOOKS like garbage (mostly non-letters,
# or a repeated-character run) is dropped before it can inflate the gate's word count or mean
# confidence, or reach the PII detectors as fake candidate text. Numeric tokens (phone
# numbers, PINs, ID fragments) are explicitly exempted -- they are often exactly the PII this
# tool needs to catch, and dropping them as "not a real word" was a real bug found this
# session (Codex, 2026-08-20).
_GARBAGE_RUN = re.compile(r"(.)\1{3,}")
_NUMERIC_TOKEN = re.compile(r"(?=.*\d)[\d()+./_-]{3,}\Z")


def _looks_like_garbage(word: str) -> bool:
    if not word:
        return True
    if _NUMERIC_TOKEN.fullmatch(word) and sum(c.isdigit() for c in word) >= 3:
        return False
    letters = sum(c.isalpha() for c in word)
    if letters / len(word) < 0.6:
        return True
    return bool(_GARBAGE_RUN.search(word))


_ocr = {}

# Tamil ("ta") and Hindi/Devanagari ("hi") have dedicated PaddleOCR models; Kannada has none
# in this version, so it stays unaddressed (Adi's explicit call, 2026-08-20). These run as
# ADDITIONAL passes over the same image, never as a replacement for the English pass: the
# OCR-confidence gate below was calibrated (2026-08-20) against English-only output on the
# real 19-image corpus, so gate scoring stays on the English pass alone -- these two only
# ever ADD extra detected lines (and therefore extra redaction coverage) on top of a page
# that already passed the gate. A page that is entirely non-Latin script and has no English
# on it at all still won't pass the gate; that is the same known limitation as the Kannada
# gap, not solved by this change.
#
# Opt-in, default off (2026-08-21) -- each language is a separate multi-submodel PaddleOCR
# pipeline, cached forever in `_ocr` once touched (see `reset_ocr_engines` below for the
# release valve). Adi's call: most of his real documents are English-only, and running these
# unconditionally on every image was the direct cause of a real OOM when a long-running eval
# process worked through many images in a row (3 OCR-language stacks + GLiNER + the address
# model, all resident at once, never released). Pass `ocr_langs=("en", "hi", "ta")` to
# `redact_file`/`redact_image` explicitly if a document actually needs them.
SUPPLEMENTARY_LANGS = ("hi", "ta")


def reset_ocr_engines() -> None:
    """Drop every cached PaddleOCR pipeline so it becomes garbage as soon as nothing else
    references it. Only matters for a long-running host process that has touched more OCR
    languages than it currently needs -- a one-shot CLI call exits and the OS reclaims this
    anyway. See `Redactor.close()` in engine.py for the equivalent release point for GLiNER
    and the address model.
    """
    _ocr.clear()


def _paddle_ocr(lang: str = "en", flat: bool = False):
    """Lazily construct (and cache) one PaddleOCR pipeline per language.

    `flat=True` turns off page unwarping and rotation and asks for word-level boxes. Unwarping
    returns coordinates in a re-drawn copy of the page, not in the image we hold, so boxes
    drift off the real text. Fine for the photo path (it has a tall safety margin); wrong for
    PDF pages, where we want boxes tight on the words.
    """
    key = (lang, flat)
    if key not in _ocr:
        local_dir = os.environ.get(MODEL_DIR_ENV)
        if local_dir:
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path(local_dir) / "paddlex_cache"))
        # PaddleX pings its model hosters on every load to check for a newer version, even
        # when the model is already cached locally -- caught correctly by this project's
        # network guard, but PaddleX hard-errors on the block instead of falling back to the
        # local cache (the same class of bug that got OPF dropped from this project
        # 2026-08-18: a dependency's own "check for updates" call defeating the offline
        # guarantee). This env var is PaddleX's own documented way to skip that check.
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR

        logging_safe.debug(f"loading PaddleOCR ({lang})")
        extra = dict(use_doc_unwarping=False, use_doc_orientation_classify=False,
                     return_word_box=True) if flat else {}
        _ocr[key] = PaddleOCR(use_angle_cls=True, lang=lang, **extra)
    return _ocr[key]


def _ocr_pass(image, lang: str, flat: bool = False):
    """Returns (texts, scores, boxes, words) -- PaddleOCR's native per-line units: a text line,
    its 0-1 confidence, and an [xmin, ymin, xmax, ymax] box, as parallel lists. `words` is a
    per-line list of (start, end, box) character ranges with their own boxes when flat, else
    a list of None.
    """
    import numpy as np

    result = _paddle_ocr(lang, flat).ocr(np.array(image))
    texts, scores, boxes, words = [], [], [], []
    for page in result:
        if not isinstance(page, dict):
            continue
        page_texts = page.get("rec_texts", [])
        texts.extend(page_texts)
        scores.extend(page.get("rec_scores", []))
        boxes.extend(page.get("rec_boxes", []))
        if flat and len(page.get("text_word", [])) == len(page_texts):
            for pieces, wboxes in zip(page["text_word"], page["text_word_boxes"]):
                pos, line = 0, []
                for piece, box in zip(pieces, wboxes):
                    line.append((pos, pos + len(piece), [int(v) for v in box]))
                    pos += len(piece)
                words.append(line)
        else:
            words.extend([None] * len(page_texts))
    return texts, scores, boxes, words


def _run_ocr(image, flat: bool = False):
    return _ocr_pass(image, "en", flat)


def _run_supplementary_ocr(image, langs=(), flat: bool = False):
    """Extra OCR passes beyond English -- see SUPPLEMENTARY_LANGS above for why these never
    touch gate scoring, and why they default to none at all. Returns the same
    (texts, scores, boxes, words) shape as _run_ocr, concatenated across every requested
    language.
    """
    texts, scores, boxes, words = [], [], [], []
    for lang in langs:
        t, s, b, w = _ocr_pass(image, lang, flat)
        texts.extend(t)
        scores.extend(s)
        boxes.extend(b)
        words.extend(w)
    return texts, scores, boxes, words


def _quality_gate(texts, scores):
    kept = [s for t, s in zip(texts, scores) for w in t.split() if not _looks_like_garbage(w)]
    mean_conf = sum(kept) / len(kept) if kept else 0.0
    return mean_conf, len(kept)


def _check_ocr_confidence(mean_conf: float, word_count: int) -> None:
    """Fail closed instead of silently returning an unredacted copy as if it were clean --
    see MIN_OCR_CONFIDENCE/MIN_OCR_WORDS above for how these floors were derived.
    """
    if word_count < MIN_OCR_WORDS:
        raise OcrEmpty(
            f"OCR read only {word_count} word(s) from this image (floor: {MIN_OCR_WORDS}). "
            "Nothing was written, because we cannot tell the difference between 'this image has "
            "no PII' and 'OCR failed to read it' -- and reporting success either way is exactly "
            "the silent under-redaction this tool exists to prevent. Try a clearer image."
        )
    if mean_conf < MIN_OCR_CONFIDENCE:
        raise OcrEmpty(
            f"OCR mean confidence was {mean_conf:.2f} (floor: {MIN_OCR_CONFIDENCE}) across "
            f"{word_count} words. Nothing was written -- a low-confidence read is more likely "
            "wrong than right, and a wrong read here means PII gets missed silently. Try a "
            "clearer image."
        )


def redact_image(redactor, in_path: str, out_path: str, force: bool = False, ocr_langs: tuple = (),
                 counts_out: dict = None, extra_terms: tuple = ()) -> str:
    """Paint solid black over every detected region. Never blur -- blur is reversible.

    `ocr_langs` -- extra OCR languages beyond the always-on English pass (e.g. ("hi", "ta")).
    Defaults to none: most documents don't need it, and each language is its own resident
    multi-submodel OCR pipeline (see SUPPLEMENTARY_LANGS above) -- opt in per call, not on by
    default.

    `counts_out`, if given, is filled with {entity_type: number found} for the receipt.
    """
    image = Image.open(in_path)
    # Phone photos routinely carry an EXIF orientation tag rather than baked-in rotation --
    # PIL ignores it by default, so a photo that looks upright in Preview/Finder can still
    # arrive at the OCR engine sideways. Apply it before anything else touches the image.
    image = ImageOps.exif_transpose(image) or image
    image = image.convert("RGB")

    image, counts = redact_pil(redactor, image, ocr_langs, extra_terms=extra_terms)
    if counts_out is not None:
        counts_out.update(counts)

    atomic_io.save_image(image, Path(out_path), force=force)
    logging_safe.info(f"wrote redacted image to {out_path}")
    return out_path


def _merge(spans):
    """Join character spans that touch or sit within 3 characters of each other."""
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 3:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def redact_pil(redactor, image, ocr_langs: tuple = (), tight: bool = False, extra_terms: tuple = ()):
    """OCR -> detect -> paint boxes on an RGB PIL image. Returns (image, {entity_type: count}).

    Shared by single images and PDF pages (pdfs.py), so both get the same OCR gate and the
    same fail-closed behaviour.

    `tight=True` boxes only the words a match touches, with a few pixels of margin, instead of
    the whole line plus a tall safety margin. It runs OCR without page unwarping so the word
    boxes sit on the pixels we hold. Used for PDF pages, which we render ourselves. Photos
    keep the generous default -- see the long comment below.
    """
    try:
        texts, scores, boxes, words = _run_ocr(image, flat=tight)
    except Exception as exc:
        raise RedactionFailed(
            f"image OCR failed ({type(exc).__name__}: {exc}). Nothing was written."
        ) from exc

    mean_conf, word_count = _quality_gate(texts, scores)
    _check_ocr_confidence(mean_conf, word_count)

    # Extra-language passes (opt-in via ocr_langs) run only after the English-based gate has
    # already passed. A failure here is the same as an English OCR failure: fail closed
    # rather than redact with only partial script coverage.
    if ocr_langs:
        try:
            extra_texts, _extra_scores, extra_boxes, extra_words = _run_supplementary_ocr(
                image, ocr_langs, flat=tight)
        except Exception as exc:
            raise RedactionFailed(
                f"supplementary-language OCR failed ({type(exc).__name__}: {exc}). Nothing was written."
            ) from exc
        texts = texts + extra_texts
        boxes = list(boxes) + list(extra_boxes)
        words = list(words) + list(extra_words)

    # One joined text, one OCR-detected line per line, so a PII span found by analyze() can
    # be traced back to the line(s) -- and therefore the box(es) -- it came from.
    joined = "\n".join(texts)
    line_starts = []
    pos = 0
    for t in texts:
        line_starts.append(pos)
        pos += len(t) + 1

    try:
        results = redactor.analyze(joined)
    except Exception as exc:
        raise RedactionFailed(
            f"a recogniser failed on OCR'd image text ({type(exc).__name__}: {exc}). Nothing "
            "was written."
        ) from exc

    if extra_terms:
        from .engine import term_hits

        results = list(results) + term_hits(joined, extra_terms)

    if tight:
        # A name's (or address's) score depends on the text around it: a name scored 0.98 alone
        # on its line but 0.34 inside a whole page, under the 0.4 cut-off. Scan each line on
        # its own too and keep what the page-wide pass missed. This only adds redactions.
        results = list(results)
        for i, line in enumerate(texts):
            if len(line.strip()) < 12:
                continue
            for r in redactor.analyze(line, threshold=LINE_RESCAN_THRESHOLD):
                # Lines read alone are noisy (list numbers became customer IDs, "Themselves"
                # a name), so only a name or an address is allowed to add a redaction.
                if r.entity_type not in ("PERSON", "ADDRESS") or not re.search(r"[^\W\d_]{3}", line[r.start:r.end]):
                    continue  # a list number like "5." is not a name
                r.start += line_starts[i]
                r.end += line_starts[i]
                if not any(o.entity_type == r.entity_type and o.start <= r.start and r.end <= o.end
                           for o in results):
                    results.append(r)

    hit_lines = {}
    for r in results:
        for i, start in enumerate(line_starts):
            end = start + len(texts[i])
            if r.start < end and start < r.end:
                hit_lines.setdefault(i, []).append((max(r.start, start) - start, min(r.end, end) - start))

    # PaddleOCR's detection box for a text line is not always a tight, correctly-placed fit
    # around the glyphs it recognised there. Found by direct pixel inspection on two real
    # photos, not assumed: a small PNG (Aadhaar card) had a bold number's box start ~20px
    # below the real digits (a fifth of a small box's own height); a large JPEG (a passport
    # photo, 4000px tall) had a name's box offset by roughly its own height. Both times the
    # box sat BELOW where the real text actually was, so redacting exactly the box left real
    # PII fully visible above it. A margin proportional to each box's own size, biased
    # upward since that is the direction every offset observed so far has gone, covers both
    # measured cases with real headroom to spare. Under-redaction is the one failure mode
    # this tool cannot have: extra blacked-out background or neighbouring text costs nothing
    # that matters; a box too small can leave PII exposed while looking redacted. Not a
    # substitute for fixing the box source if a more precise cause is found later -- a
    # deliberately generous mitigation in the meantime.
    draw = ImageDraw.Draw(image)
    for i, spans in hit_lines.items():
        raw = [int(v) for v in boxes[i]]
        # A box's own [x1,y1,x2,y2] is not guaranteed x1<=x2, y1<=y2 -- a rotated or
        # otherwise unusual detection region can come back inverted. Normalise before doing
        # any arithmetic on it, or a negative height/width can silently invert the final
        # margin-expanded rectangle too, which PIL raises on rather than draws.
        x1, x2 = min(raw[0], raw[2]), max(raw[0], raw[2])
        y1, y2 = min(raw[1], raw[3]), max(raw[1], raw[3])
        h, w = y2 - y1, x2 - x1
        margin_x = max(10, w // 6)
        margin_top, margin_bottom = max(20, h * 2), max(15, h)
        if tight and words[i]:
            # Cover exactly the words the span touches, plus a few pixels for anti-aliasing.
            pad = max(3, h // 8)
            for a, b in _merge(spans):
                hit = [box for ws, we, box in words[i] if ws < b and a < we]
                if not hit:
                    continue
                draw.rectangle([max(0, min(c[0] for c in hit) - pad), max(0, min(c[1] for c in hit) - pad),
                                min(image.width, max(c[2] for c in hit) + pad),
                                min(image.height, max(c[3] for c in hit) + pad)], fill=(0, 0, 0))
            continue
        # A box can also come back with coordinates outside the image entirely (seen on a
        # large rotated JPEG that PaddleOCR internally downsizes then rescales for
        # detection -- rounding in that rescale can push a box's edge past the real bounds).
        # Clamping each edge to the image individually, then re-sorting the final pair, means
        # a box that was ever out of bounds cannot invert top/bottom or left/right, whatever
        # combination of margin and clamping produced it.
        left, right = sorted((max(0, min(image.width, x1 - margin_x)), max(0, min(image.width, x2 + margin_x))))
        top, bottom = sorted((max(0, min(image.height, y1 - margin_top)), max(0, min(image.height, y2 + margin_bottom))))
        draw.rectangle(
            [left, top, right, bottom],
            fill=(0, 0, 0),
        )

    from .engine import count_spans

    return image, count_spans(results)
