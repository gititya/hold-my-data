"""PDF redaction: render each page to an image, run the image path, write a flattened PDF.

Flattened on purpose. Editing a PDF's text layer in place can leave the original text behind
in fonts, metadata or an invisible OCR layer. A page rebuilt from pixels has none of that,
so what you see is all there is. The cost is that the output has no selectable text.

Rendering uses pypdfium2 (Apache-2.0/BSD, the PDFium engine Chrome uses). No system binary.
Every non-blank page goes through the same OCR gate as a single image, so a page too unclear
to read refuses the whole file rather than passing through unredacted.
"""

from pathlib import Path

from PIL import ImageStat

from . import atomic_io, logging_safe
from .engine import RedactionFailed
from .images import redact_pil

# 200 DPI: OCR reads body text reliably here; 300 roughly doubles the time per page.
RENDER_DPI = 200
MAX_SIDE_PX = 4000

# A page with no ink has nothing to leak and nothing for OCR to read, so the OCR gate would
# refuse it. Luminance spread this low means a uniform page (blank sheet, blank scan back).
_BLANK_STDDEV = 2.0


def _is_blank(image) -> bool:
    return ImageStat.Stat(image.convert("L")).stddev[0] < _BLANK_STDDEV


def redact_pdf(redactor, in_path: str, out_path: str, force: bool = False, ocr_langs: tuple = (),
               counts_out: dict = None, on_page=None, extra_terms: tuple = ()) -> str:
    """Redact every page of `in_path` into a new flattened PDF at `out_path`.

    `on_page(page_number, total)` is called before each page, for progress display.
    `counts_out`, if given, is filled with {entity_type: number found} across all pages.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RedactionFailed(
            "PDF support needs the pypdfium2 package, which is not installed. Nothing was written."
        ) from exc

    out = Path(out_path)
    # Fail before minutes of OCR, not after.
    atomic_io._check_clobber(out, force)

    try:
        pdf = pdfium.PdfDocument(str(in_path))
    except pdfium.PdfiumError as exc:
        reason = "is locked with a password" if "password" in str(exc).lower() else "could not be opened (it may be damaged)"
        raise RedactionFailed(f"this PDF {reason}. Nothing was written.") from exc
    pdf.init_forms()  # draw filled form fields, as Preview does

    pages, counts = [], {}
    try:
        total = len(pdf)
        if total == 0:
            raise RedactionFailed("this PDF has no pages. Nothing was written.")
        for n in range(total):
            if on_page:
                on_page(n + 1, total)
            page = pdf[n]
            w, h = page.get_size()
            # OCR shrinks anything over 4000 px anyway; don't render pixels it throws away
            scale = min(RENDER_DPI / 72, MAX_SIDE_PX / max(w, h))
            image = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
            if _is_blank(image):
                logging_safe.debug(f"page {n + 1}: blank, kept as is")
                pages.append(image)
                continue
            try:
                image, page_counts = redact_pil(redactor, image, ocr_langs, tight=True, extra_terms=extra_terms)
            except RedactionFailed as exc:
                raise type(exc)(f"page {n + 1} of {total}: {exc}") from exc
            for entity, count in page_counts.items():
                counts[entity] = counts.get(entity, 0) + count
            pages.append(image)
    finally:
        pdf.close()

    # ponytail: all pages are held in memory (about 12 MB each at 200 DPI); stream to disk
    # if 100+ page files matter.
    atomic_io.save_image(
        pages[0], out, force=force, save_all=True, append_images=pages[1:], resolution=RENDER_DPI
    )
    if counts_out is not None:
        counts_out.update(counts)
    logging_safe.info(f"wrote redacted pdf to {out} ({len(pages)} pages)")
    return str(out)
