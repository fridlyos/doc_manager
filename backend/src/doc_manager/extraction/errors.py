"""Extraction error codes.

Encrypted, malformed, empty, or OCR-only files get a *specific* code so they can
stay visible in the error queue rather than silently disappearing (TECHSTACK
section 5.5). The code is stable and machine-readable; the message is advisory.
"""

from __future__ import annotations

from enum import StrEnum


class ExtractionErrorCode(StrEnum):
    empty_file = "empty_file"
    encrypted = "encrypted"
    malformed = "malformed"
    unsupported_encoding = "unsupported_encoding"
    #: The file parsed but yielded no extractable text — typically a scanned or
    #: image-only PDF that would need OCR (a deferred adapter).
    no_extractable_text = "no_extractable_text"
    #: No extractor is registered for this file's extension.
    unsupported_type = "unsupported_type"


class ExtractionError(Exception):
    """Raised by an extractor for a per-document, isolatable failure."""

    def __init__(self, code: ExtractionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
