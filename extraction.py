"""
extraction.py

PDF text/OCR extraction + structured data extraction + validation.

Two text-extraction paths with different PII handling, by design:

- Path A (digital PDF, has a text layer): per-page text extraction via
  pypdf. This text goes through pii_guard.py redaction before it is ever
  sent to an LLM. Full PII protection. All redacted pages are then
  concatenated into ONE structured-extraction call (see
  build_extraction_input() / extract_structured()) rather than one call per
  page -- this is deliberate: Gemini API quota (requests/day, requests/
  minute) is the binding constraint here, not context length, and a typical
  lab report's full text comfortably fits in a single call. Falls back to
  splitting into a couple of calls only if the combined text is unusually
  large (see _MAX_SINGLE_CALL_CHARS).

- Path B (scanned/photographed PDF, no usable text layer): if a page's
  extracted text falls below MIN_CHARS_PER_PAGE, the page is rendered as an
  image and sent DIRECTLY to a vision-capable Gemini call -- no image-level
  PII redaction is performed. This is a deliberate, accepted tradeoff (see
  README.md section 3 and CLAUDE.md): reliable PII blackout on scanned
  images was evaluated and produced false positives on lab test names
  (e.g. "Glucose" flagged as a name). Callers (app.py) MUST disclose this to
  the user distinctly from Path A's messaging -- see PageExtraction.path.
  Page images can't be concatenated the way text can, so Path B still makes
  one call per scanned page, bounded-concurrent via a thread pool.

Structured extraction never decides status. The LLM extracts test
name/value/unit/range-as-printed only; reference_ranges.py's
parse_range_string() + compute_status() do the rest in pure Python.
"""

from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

import fitz  # PyMuPDF, used for both text yield checks and page rendering
from langchain_core.messages import HumanMessage, SystemMessage
from pypdf import PdfReader

from llm_utils import extract_text
from model_config import get_llm_for_task
from models import ExtractionResult, TestResult
from prompts import EXTRACTION_SYSTEM_PROMPT
from reference_ranges import compute_status, lookup_fallback_range, parse_range_string
from retry import external_call_retry

MIN_CHARS_PER_PAGE = 40
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15MB
MAX_PAGE_COUNT = 30

# Path B (scanned images) only -- one call per scanned page is unavoidable,
# so this caps how many in-flight Gemini vision calls a single report can
# open at once.
MAX_CONCURRENT_PAGE_CALLS = 5

# Path A (digital text) is sent as ONE concatenated extraction call by
# default -- see build_extraction_input(). If the combined text is larger
# than this, split it into 2 halves and issue 2 calls instead, rather than
# risking one oversized call. Conservative on purpose: this is a rare
# edge case (a very long report), not the default path.
_MAX_SINGLE_CALL_CHARS = 100_000

ExtractionPath = Literal["digital_text", "scanned_image"]


class FileValidationError(Exception):
    """Raised for user-facing, friendly-message file rejections."""


@dataclass
class PageExtraction:
    page_number: int
    text: str
    path: ExtractionPath


@dataclass
class LoadedDocument:
    pages: list[PageExtraction]

    @property
    def scanned_page_numbers(self) -> list[int]:
        return [p.page_number for p in self.pages if p.path == "scanned_image"]


def validate_upload(file_bytes: bytes) -> None:
    """
    Raises FileValidationError with a friendly message on rejection.
    Never lets a raw exception (corrupt PDF, etc.) bubble to the UI.
    """
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise FileValidationError(
            "That file is a bit too large (max 15MB). "
            "Please try a smaller or lower-resolution scan."
        )

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        page_count = len(reader.pages)
    except Exception as exc:
        raise FileValidationError(
            "We couldn't read this file, please try a clearer scan."
        ) from exc

    if page_count == 0:
        raise FileValidationError(
            "This file doesn't seem to have any pages. Please try a different file."
        )

    if page_count > MAX_PAGE_COUNT:
        raise FileValidationError(
            f"This report has too many pages (max {MAX_PAGE_COUNT}). "
            "Please upload a shorter report."
        )


def load_pdf_text(file_bytes: bytes) -> LoadedDocument:
    """
    Extracts text per page. Pages with low text yield are flagged as
    scanned_image (Path B) -- their `text` field is left empty here; the
    caller (ocr_fallback) fills it in via the vision LLM.
    """
    pages: list[PageExtraction] = []
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if len(text) >= MIN_CHARS_PER_PAGE:
                pages.append(PageExtraction(page_number=i, text=text, path="digital_text"))
            else:
                pages.append(PageExtraction(page_number=i, text="", path="scanned_image"))
    finally:
        doc.close()
    return LoadedDocument(pages=pages)


def _render_page_as_png_b64(file_bytes: bytes, page_number: int) -> str:
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=200)
        png_bytes = pix.tobytes("png")
    finally:
        doc.close()
    return base64.b64encode(png_bytes).decode("utf-8")


@external_call_retry
def _vision_extract_page_text(image_b64: str) -> str:
    """
    Sends a rendered page image directly to Gemini vision, no redaction.
    Path B only -- caller is responsible for having disclosed this to the
    user before invoking it.
    """
    llm = get_llm_for_task("extraction")
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    "Transcribe all visible text from this health report page "
                    "as plainly as possible, preserving test names, values, "
                    "units, and reference ranges exactly as shown."
                ),
            },
            {
                "type": "image_url",
                "image_url": f"data:image/png;base64,{image_b64}",
            },
        ]
    )
    response = llm.invoke([message])
    return extract_text(response.content)


def _ocr_one_page(file_bytes: bytes, page: PageExtraction) -> PageExtraction:
    image_b64 = _render_page_as_png_b64(file_bytes, page.page_number)
    text = _vision_extract_page_text(image_b64)
    return PageExtraction(page_number=page.page_number, text=text, path="scanned_image")


def ocr_fallback(file_bytes: bytes, document: LoadedDocument) -> LoadedDocument:
    """
    Fills in `text` for every page flagged as scanned_image by sending that
    page's rendered image directly to the vision LLM. No PII redaction is
    applied on this path -- see module docstring.

    Pages are OCR'd concurrently (independent network calls) rather than one
    at a time, since sequential per-page calls were the main source of slow
    processing on multi-page reports.
    """
    pages_to_ocr = [p for p in document.pages if p.path == "scanned_image" and not p.text]
    if not pages_to_ocr:
        return document

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PAGE_CALLS) as executor:
        ocr_results = dict(
            zip(
                (p.page_number for p in pages_to_ocr),
                executor.map(lambda p: _ocr_one_page(file_bytes, p), pages_to_ocr),
            )
        )

    updated_pages = [
        ocr_results[page.page_number] if page.page_number in ocr_results else page
        for page in document.pages
    ]
    return LoadedDocument(pages=updated_pages)


def _finalize_test_result(test: TestResult) -> TestResult:
    """Fills in range_low/range_high/range_source/status via pure Python."""
    low, high = parse_range_string(test.range_raw)
    source = "report"

    if low is None and high is None:
        fallback = lookup_fallback_range(test.test_name)
        if fallback is not None:
            low, high = fallback
            source = "fallback_table"
        else:
            source = "unavailable"

    status = compute_status(test.value, low, high)
    return test.model_copy(
        update={
            "range_low": low,
            "range_high": high,
            "range_source": source,
            "status": status,
        }
    )


def build_extraction_input(pages: list[PageExtraction]) -> str:
    """
    Concatenates multiple pages' text into one string, with page markers,
    for a single structured-extraction call. Order follows the input list
    as given -- callers should pass pages already in document order.
    """
    return "\n\n".join(f"=== Page {p.page_number} ===\n{p.text}" for p in pages)


@external_call_retry
def _extract_from_text(text: str) -> ExtractionResult:
    llm = get_llm_for_task("extraction").with_structured_output(ExtractionResult)
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=text),
    ]
    result = llm.invoke(messages)
    assert isinstance(result, ExtractionResult)
    return result


def _merge_extraction_results(results: list[ExtractionResult]) -> ExtractionResult:
    """
    Merges multiple ExtractionResults (from the rare multi-call split path)
    into one, de-duplicating by test_name -- keeps the last occurrence in
    input order, warns on value mismatch between duplicates (could indicate
    a merge issue).
    """
    merged_tests: dict[str, TestResult] = {}
    free_text_notes: list[str] = []
    warnings: list[str] = []

    for result in results:
        warnings.extend(result.extraction_warnings)
        if result.free_text_notes:
            free_text_notes.append(result.free_text_notes)

        for test in result.tests:
            key = test.test_name.strip().lower()
            if key in merged_tests and merged_tests[key].value != test.value:
                warnings.append(
                    f"Duplicate test '{test.test_name}' had differing values "
                    f"({merged_tests[key].value} vs {test.value}) across pages; "
                    "kept the later occurrence."
                )
            merged_tests[key] = test

    return ExtractionResult(
        tests=list(merged_tests.values()),
        free_text_notes="\n\n".join(free_text_notes) if free_text_notes else None,
        extraction_warnings=warnings,
    )


def extract_structured(document: LoadedDocument) -> ExtractionResult:
    """
    Sends all of a document's text to Gemini in ONE structured-extraction
    call by default -- Gemini API quota (requests/day, requests/minute) is
    the binding constraint here, not context length, and a typical lab
    report's full text comfortably fits in a single call. This replaces the
    previous one-call-per-page approach, which could burn most of a day's
    request quota on a single multi-page report.

    Safety fallback: if the combined text is unusually large
    (> _MAX_SINGLE_CALL_CHARS), split the pages into 2 halves and issue 2
    calls instead of 1, then merge -- a rare edge case, not the default
    path. Computes range/status in Python after the LLM call(s), never
    before.
    """
    pages_with_text = [page for page in document.pages if page.text.strip()]
    if not pages_with_text:
        return ExtractionResult(tests=[], free_text_notes=None, extraction_warnings=[])

    combined_text = build_extraction_input(pages_with_text)

    if len(combined_text) <= _MAX_SINGLE_CALL_CHARS:
        extraction_result = _extract_from_text(combined_text)
    else:
        midpoint = len(pages_with_text) // 2
        halves = [pages_with_text[:midpoint], pages_with_text[midpoint:]]
        chunk_results = [_extract_from_text(build_extraction_input(half)) for half in halves]
        extraction_result = _merge_extraction_results(chunk_results)

    finalized_tests = [_finalize_test_result(t) for t in extraction_result.tests]

    return ExtractionResult(
        tests=finalized_tests,
        free_text_notes=extraction_result.free_text_notes,
        extraction_warnings=extraction_result.extraction_warnings,
    )
