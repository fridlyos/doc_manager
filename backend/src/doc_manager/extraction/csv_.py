"""CSV extractor: a row-aware text representation with the header repeated.

Each section renders up to ``_ROWS_PER_SECTION`` rows as ``col: value`` lines so
that retrieval sees column context, and the header is repeated at the top of
every section so a chunk taken from the middle of a large file stays readable
(TECHSTACK section 5.5).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from charset_normalizer import from_bytes

from doc_manager.extraction.base import ExtractedDocument, ExtractedPage
from doc_manager.extraction.errors import ExtractionError, ExtractionErrorCode

_ROWS_PER_SECTION = 100


class CsvExtractor:
    name = "csv"
    version = "csv-1"

    def extract(self, path: Path) -> ExtractedDocument:
        data = path.read_bytes()
        if not data.strip():
            raise ExtractionError(ExtractionErrorCode.empty_file, "CSV is empty.")
        best = from_bytes(data).best()
        if best is None:
            raise ExtractionError(ExtractionErrorCode.unsupported_encoding, "could not decode CSV.")
        try:
            rows = list(csv.reader(io.StringIO(str(best))))
        except csv.Error as exc:
            raise ExtractionError(ExtractionErrorCode.malformed, f"malformed CSV: {exc}") from exc

        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if not rows:
            raise ExtractionError(ExtractionErrorCode.empty_file, "CSV has no rows.")

        header = rows[0]
        body = rows[1:]
        if not body:
            # Header-only file: emit the header itself as the single section.
            pages = [ExtractedPage(index=0, page_number=None, text=" | ".join(header))]
        else:
            pages = [
                ExtractedPage(index=i, page_number=None, text=self._render(header, chunk))
                for i, chunk in enumerate(_batches(body, _ROWS_PER_SECTION))
            ]
        return ExtractedDocument(
            extractor_name=self.name,
            extractor_version=self.version,
            pages=pages,
            metadata={
                "encoding": best.encoding,
                "row_count": len(body),
                "column_count": len(header),
            },
        )

    @staticmethod
    def _render(header: list[str], chunk: list[list[str]]) -> str:
        lines = [" | ".join(header)]
        for row in chunk:
            pairs = [f"{header[i]}: {value}" for i, value in enumerate(row) if i < len(header)]
            lines.append(" | ".join(pairs))
        return "\n".join(lines)


def _batches(rows: list[list[str]], size: int) -> list[list[list[str]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]
