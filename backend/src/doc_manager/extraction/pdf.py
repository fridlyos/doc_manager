"""PDF extractor using PyMuPDF, preserving one-based page numbers.

A page with no selectable text (scanned/image-only) can optionally be recovered
with OCR: when ``PdfOcrOptions`` is supplied, PyMuPDF rasterises the page and runs
Tesseract on it. OCR is per-page and only fires on empty pages, so text-layer PDFs
pay nothing. The engine (``tesseract``) and its language data must be installed in
the worker image; see ``deploy/worker.Dockerfile``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from doc_manager.extraction.base import ExtractedDocument, ExtractedPage
from doc_manager.extraction.errors import ExtractionError, ExtractionErrorCode, OcrRequired


@dataclass(frozen=True, slots=True)
class PdfOcrOptions:
    """OCR tuning for scanned pages. Presence of this object enables OCR."""

    language: str = "eng"
    dpi: int = 300
    #: Safety cap: OCR is slow on CPU, so stop OCR-ing after this many empty pages
    #: in one document (the rest stay un-OCR'd rather than blocking the queue).
    max_pages: int = 50


class PdfExtractor:
    name = "pdf"

    def __init__(self, ocr: PdfOcrOptions | None = None, *, defer_ocr: bool = False) -> None:
        self._ocr = ocr
        #: With OCR enabled, raise ``OcrRequired`` at the first page that would
        #: need OCR instead of running it, so the slow work moves to the OCR lane.
        #: Output and version are unchanged: the OCR lane re-extracts the whole
        #: file with this same extractor version.
        self._defer_ocr = defer_ocr
        # Version participates in the re-index trigger (TECHSTACK 7.3): toggling OCR
        # changes extraction output, so OCR runs carry a distinct version and a
        # later enable/disable forces affected documents to re-index.
        self.version = "pdf-1-ocr" if ocr is not None else "pdf-1"

    def extract(self, path: Path) -> ExtractedDocument:
        if path.stat().st_size == 0:
            raise ExtractionError(ExtractionErrorCode.empty_file, "PDF is empty.")
        try:
            doc = pymupdf.open(path)
        except Exception as exc:  # pymupdf raises assorted low-level errors
            raise ExtractionError(
                ExtractionErrorCode.malformed, f"could not open PDF: {exc}"
            ) from exc
        try:
            if doc.needs_pass:
                raise ExtractionError(ExtractionErrorCode.encrypted, "PDF is password-protected.")
            pages = self._extract_pages(doc)
            raw = doc.metadata or {}
        finally:
            doc.close()

        if not any(page.text.strip() for page in pages):
            # Parsed fine but no selectable text even after any OCR: image-only.
            hint = "" if self._ocr is not None else " Enable OCR (DOCMAN_OCR_ENABLED) to index it."
            raise ExtractionError(
                ExtractionErrorCode.no_extractable_text,
                "PDF has no extractable text (likely scanned/image-only)." + hint,
            )

        metadata = {
            "page_count": len(pages),
            "pymupdf_version": pymupdf.__version__,
            "title": raw.get("title") or None,
            "author": raw.get("author") or None,
            "ocr": self._ocr is not None,
        }
        return ExtractedDocument(
            extractor_name=self.name,
            extractor_version=self.version,
            pages=pages,
            metadata=metadata,
        )

    def _extract_pages(self, doc: pymupdf.Document) -> list[ExtractedPage]:
        pages: list[ExtractedPage] = []
        ocr_done = 0
        for index in range(doc.page_count):
            page = doc[index]
            text = page.get_text("text")
            if not text.strip() and self._ocr is not None and ocr_done < self._ocr.max_pages:
                if self._defer_ocr:
                    raise OcrRequired(f"page {index + 1} has no text layer")
                text = self._ocr_page(page)
                ocr_done += 1
            pages.append(ExtractedPage(index=index, page_number=index + 1, text=text))
        return pages

    def _ocr_page(self, page: pymupdf.Page) -> str:
        """OCR one page with Tesseract via PyMuPDF; raise ``ocr_failed`` if the
        engine is unavailable or crashes (a config problem, not an image-only doc)."""
        assert self._ocr is not None
        try:
            textpage = page.get_textpage_ocr(
                flags=0, language=self._ocr.language, dpi=self._ocr.dpi, full=True
            )
            return str(page.get_text("text", textpage=textpage))
        except Exception as exc:
            raise ExtractionError(
                ExtractionErrorCode.ocr_failed,
                f"OCR failed (Tesseract missing or misconfigured?): {exc}",
            ) from exc
