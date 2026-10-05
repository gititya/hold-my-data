import pytest
from PIL import Image

from holdmydata import atomic_io
from holdmydata.engine import RedactionFailed
from holdmydata.pdfs import redact_pdf


def _blank_pdf(path, pages=2):
    imgs = [Image.new("RGB", (400, 500), "white") for _ in range(pages)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:])


def test_blank_pages_pass_through_without_ocr(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _blank_pdf(src, pages=2)
    counts = {}

    # a blank page never reaches OCR, so no redactor is needed
    redact_pdf(None, str(src), str(dst), counts_out=counts)

    assert dst.exists() and counts == {}
    pdfium = pytest.importorskip("pypdfium2")
    assert len(pdfium.PdfDocument(str(dst))) == 2


def test_existing_output_is_refused_before_any_work(tmp_path):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _blank_pdf(src)
    dst.write_bytes(b"keep me")

    with pytest.raises(atomic_io.WouldOverwrite):
        redact_pdf(None, str(src), str(dst))
    assert dst.read_bytes() == b"keep me"


def test_damaged_pdf_fails_closed(tmp_path):
    src, dst = tmp_path / "bad.pdf", tmp_path / "out.pdf"
    src.write_bytes(b"not a pdf")

    with pytest.raises(RedactionFailed):
        redact_pdf(None, str(src), str(dst))
    assert not dst.exists()


def test_output_has_no_text_layer(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _blank_pdf(src, pages=1)
    redact_pdf(None, str(src), str(dst))
    assert pdfium.PdfDocument(str(dst))[0].get_textpage().get_text_range() == ""
