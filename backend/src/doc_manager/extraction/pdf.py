"""PDF extractor using PyMuPDF, preserving one-based page numbers."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from doc_manager.extraction.base import ExtractedDocument, ExtractedPage
from doc_manager.extraction.errors import ExtractionError, ExtractionErrorCode


class PdfExtractor:
    name = "pdf"
    #: Bump when extraction logic changes so downstream re-indexes (TECHSTACK 7.3).
    version = "pdf-1"

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
            pages: list[ExtractedPage] = []
            for index in range(doc.page_count):
                text = doc[index].get_text("text")
                pages.append(ExtractedPage(index=index, page_number=index + 1, text=text))
            raw = doc.metadata or {}
        finally:
            doc.close()

        if not any(page.text.strip() for page in pages):
            # Parsed fine but no selectable text: scanned/image-only, needs OCR.
            raise ExtractionError(
                ExtractionErrorCode.no_extractable_text,
                "PDF has no extractable text (likely scanned/image-only).",
            )

        metadata = {
            "page_count": len(pages),
            "pymupdf_version": pymupdf.__version__,
            "title": raw.get("title") or None,
            "author": raw.get("author") or None,
        }
        return ExtractedDocument(
            extractor_name=self.name,
            extractor_version=self.version,
            pages=pages,
            metadata=metadata,
        )
