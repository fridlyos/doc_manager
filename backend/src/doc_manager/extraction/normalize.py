"""Versioned normalization of extracted documents (TECHSTACK section 5.4).

Produces two hashes with deliberately different sensitivity:

- **text_hash** — pagination-*insensitive*. Page boundaries are dissolved and
  whitespace collapsed, so the same text laid out across different page splits
  hashes identically. Drives text-duplicate detection.
- **structure_hash** — pagination-*sensitive*, computed over the ordered
  normalized page/section records. Two text-equivalent files with different
  pagination differ here, keeping citations correct (artifacts/chunks/embeddings
  are only reused when structure hashes match).

``NORMALIZATION_VERSION`` is part of the artifact/content identity: bumping it
forces re-normalization and re-indexing (TECHSTACK 7.3).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from doc_manager.extraction.base import ExtractedDocument

#: Bump when the normalization rules below change.
NORMALIZATION_VERSION = "norm-1"

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizedPage:
    index: int
    page_number: int | None
    text: str


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    pages: list[NormalizedPage]
    text_hash: str
    structure_hash: str
    normalization_version: str

    @property
    def character_count(self) -> int:
        return sum(len(page.text) for page in self.pages)


def normalize_text(text: str) -> str:
    """NFC-normalize and collapse all whitespace runs to single spaces."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def normalize(doc: ExtractedDocument) -> NormalizedDocument:
    pages = [
        NormalizedPage(
            index=page.index, page_number=page.page_number, text=normalize_text(page.text)
        )
        for page in doc.pages
    ]

    # Pagination-insensitive: concatenate every page's text and re-collapse so
    # page boundaries (and any word split across them) disappear.
    flat = normalize_text(" ".join(page.text for page in pages))
    text_hash = hashlib.sha256(flat.encode("utf-8")).hexdigest()

    # Pagination-sensitive: canonical JSON of the ordered records including their
    # page numbers, so different splits produce a different hash.
    structure_payload = json.dumps(
        [[page.page_number, page.text] for page in pages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    structure_hash = hashlib.sha256(structure_payload.encode("utf-8")).hexdigest()

    return NormalizedDocument(
        pages=pages,
        text_hash=text_hash,
        structure_hash=structure_hash,
        normalization_version=NORMALIZATION_VERSION,
    )
