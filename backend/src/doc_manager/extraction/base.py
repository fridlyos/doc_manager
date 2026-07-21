"""Extractor interface and result records (TECHSTACK section 5.5).

An extractor turns one source file into ordered page/section records plus
document metadata and warnings, tagged with the extractor's name and version so
a change in the extractor can force re-indexing later (TECHSTACK 7.3). It never
touches the database or the filesystem beyond reading the given path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One page (PDF) or synthetic section (text/CSV) of a document."""

    #: 0-based ordinal within the document; always dense and gap-free.
    index: int
    #: 1-based physical page number for paginated formats (PDF); ``None`` for
    #: formats without real pages (TXT/MD/CSV/log use synthetic sections).
    page_number: int | None
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    extractor_name: str
    extractor_version: str
    pages: list[ExtractedPage]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def character_count(self) -> int:
        return sum(len(page.text) for page in self.pages)


@runtime_checkable
class Extractor(Protocol):
    """Stateless, reusable extractor for one or more file extensions."""

    name: str
    version: str

    def extract(self, path: Path) -> ExtractedDocument: ...
