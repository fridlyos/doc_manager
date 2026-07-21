"""Extractor registry: dispatch by lowercase, dot-less extension.

The set of registered extensions is the source of truth for "supported types".
An unregistered extension yields ``None`` so the caller can mark the catalog
entry ``unsupported`` rather than failing the job.
"""

from __future__ import annotations

from doc_manager.extraction.base import Extractor
from doc_manager.extraction.csv_ import CsvExtractor
from doc_manager.extraction.pdf import PdfExtractor
from doc_manager.extraction.text import TextExtractor

_PDF = PdfExtractor()
_TEXT = TextExtractor()
_CSV = CsvExtractor()

_BY_EXTENSION: dict[str, Extractor] = {
    "pdf": _PDF,
    "txt": _TEXT,
    "md": _TEXT,
    "log": _TEXT,
    "csv": _CSV,
}

#: Extensions with a registered extractor (matches scanner DEFAULT_INCLUDE set).
SUPPORTED_EXTENSIONS = frozenset(_BY_EXTENSION)


def get_extractor(extension: str) -> Extractor | None:
    """Return the extractor for an extension, or None if unsupported."""
    return _BY_EXTENSION.get(extension.lower().lstrip("."))
