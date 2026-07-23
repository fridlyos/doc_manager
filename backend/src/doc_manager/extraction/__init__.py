"""Document extractors (TECHSTACK section 5.5).

Turns a source file into ordered page/section records with metadata, warnings,
and an extractor version. Pure library — no DB, no vector store; the index_file
job (Phase 3.c+) drives it and persists the results.
"""

from doc_manager.extraction.base import ExtractedDocument, ExtractedPage, Extractor
from doc_manager.extraction.errors import ExtractionError, ExtractionErrorCode
from doc_manager.extraction.registry import (
    SUPPORTED_EXTENSIONS,
    get_extractor,
)

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ExtractedDocument",
    "ExtractedPage",
    "ExtractionError",
    "ExtractionErrorCode",
    "Extractor",
    "get_extractor",
]
