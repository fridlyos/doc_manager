"""Extractor registry: dispatch by lowercase, dot-less extension.

The set of registered extensions is the source of truth for "supported types".
An unregistered extension yields ``None`` so the caller can mark the catalog
entry ``unsupported`` rather than failing the job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from doc_manager.extraction.base import Extractor
from doc_manager.extraction.csv_ import CsvExtractor
from doc_manager.extraction.docx_ import DocxExtractor
from doc_manager.extraction.pdf import PdfExtractor, PdfOcrOptions
from doc_manager.extraction.text import TextExtractor

if TYPE_CHECKING:
    from doc_manager.core.config import Settings

_PDF = PdfExtractor()
_TEXT = TextExtractor()
_CSV = CsvExtractor()
_DOCX = DocxExtractor()

_BY_EXTENSION: dict[str, Extractor] = {
    "pdf": _PDF,
    "txt": _TEXT,
    "md": _TEXT,
    "csv": _CSV,
    "docx": _DOCX,
}

#: Extensions with a registered extractor (matches scanner DEFAULT_INCLUDE set).
SUPPORTED_EXTENSIONS = frozenset(_BY_EXTENSION)


def get_extractor(
    extension: str, *, settings: Settings | None = None, defer_ocr: bool = False
) -> Extractor | None:
    """Return the extractor for an extension, or None if unsupported.

    When ``settings`` enables OCR, the PDF extractor is built with OCR so scanned
    pages are recovered; every other extractor (and the OCR-off case) uses the
    shared stateless singleton. ``defer_ocr`` makes that PDF extractor raise
    ``OcrRequired`` rather than OCR inline (see the OCR lane in ``index_file``).
    """
    ext = extension.lower().lstrip(".")
    if ext == "pdf" and settings is not None and settings.ocr_enabled:
        return PdfExtractor(
            PdfOcrOptions(
                language=settings.ocr_language,
                dpi=settings.ocr_dpi,
                max_pages=settings.ocr_max_pages,
            ),
            defer_ocr=defer_ocr,
        )
    return _BY_EXTENSION.get(ext)
