"""Plain-text extractor for TXT, Markdown, and log files.

Decodes with encoding detection and splits into synthetic sections on blank-line
boundaries. Text formats have no physical pages, so ``page_number`` is ``None``;
the dense ``index`` gives each section a stable ordinal for chunking.
"""

from __future__ import annotations

import re
from pathlib import Path

from charset_normalizer import from_bytes

from doc_manager.extraction.base import ExtractedDocument, ExtractedPage
from doc_manager.extraction.errors import ExtractionError, ExtractionErrorCode

_SECTION_SPLIT = re.compile(r"\n[ \t]*\n")


class TextExtractor:
    name = "text"
    version = "text-1"

    def extract(self, path: Path) -> ExtractedDocument:
        data = path.read_bytes()
        if not data.strip():
            raise ExtractionError(ExtractionErrorCode.empty_file, "file is empty.")
        best = from_bytes(data).best()
        if best is None:
            raise ExtractionError(
                ExtractionErrorCode.unsupported_encoding, "could not decode text."
            )
        content = str(best)
        sections = [block.strip() for block in _SECTION_SPLIT.split(content)]
        sections = [block for block in sections if block]
        if not sections:  # content was only whitespace once decoded
            raise ExtractionError(ExtractionErrorCode.empty_file, "file has no text.")
        pages = [
            ExtractedPage(index=i, page_number=None, text=block) for i, block in enumerate(sections)
        ]
        return ExtractedDocument(
            extractor_name=self.name,
            extractor_version=self.version,
            pages=pages,
            metadata={"encoding": best.encoding, "section_count": len(pages)},
        )
