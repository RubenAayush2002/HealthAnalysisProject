import io

import pytest
from pypdf import PdfWriter

from extraction import (
    MAX_FILE_SIZE_BYTES,
    MAX_PAGE_COUNT,
    FileValidationError,
    validate_upload,
)


def _make_pdf_bytes(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_valid_pdf_passes():
    validate_upload(_make_pdf_bytes(1))  # should not raise


def test_oversized_file_rejected_with_friendly_message():
    oversized = b"%PDF-1.4\n" + b"0" * (MAX_FILE_SIZE_BYTES + 1)
    with pytest.raises(FileValidationError) as exc_info:
        validate_upload(oversized)
    assert "large" in str(exc_info.value).lower()


def test_wrong_type_rejected_with_friendly_message():
    not_a_pdf = b"this is just plain text, not a pdf at all"
    with pytest.raises(FileValidationError) as exc_info:
        validate_upload(not_a_pdf)
    assert "couldn't read" in str(exc_info.value).lower()


def test_too_many_pages_rejected_with_friendly_message():
    too_many = _make_pdf_bytes(MAX_PAGE_COUNT + 1)
    with pytest.raises(FileValidationError) as exc_info:
        validate_upload(too_many)
    assert "too many pages" in str(exc_info.value).lower()


def test_no_raw_exception_bubbles_for_corrupt_file():
    corrupt = b"%PDF-1.4\ngarbage garbage garbage"
    with pytest.raises(FileValidationError):
        validate_upload(corrupt)
