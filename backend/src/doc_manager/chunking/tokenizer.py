"""Token counting for the chunker (TECHSTACK section 5.7).

The chunker sizes chunks in *tokens*, so "token" must mean exactly one thing for
a given chunking profile. This module defines the ``Tokenizer`` interface and a
pure, dependency-free default. The tokenizer's ``id`` is part of the chunking
profile hash, so swapping tokenizers (e.g. to the embedding model's own
wordpiece tokenizer in Phase 4.b) yields a *new* profile rather than silently
re-sizing existing chunks.

``WhitespaceTokenizer`` is lossless on normalized text: normalization already
collapses every whitespace run to a single space and strips ends (see
``extraction.normalize.normalize_text``), so ``" ".join(tokenize(t)) == t`` for
any normalized ``t``. That makes chunk splitting exact and reversible.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Tokenizer(Protocol):
    """Deterministic, stateless token splitter/counter."""

    #: Stable identity folded into the chunking profile hash. Bump on any change
    #: to the tokenization rules so a re-tokenization forces a new profile.
    id: str

    def tokenize(self, text: str) -> list[str]:
        """Split text into tokens. ``count`` is ``len(tokenize(text))``."""
        ...

    def count(self, text: str) -> int:
        """Number of tokens in ``text``."""
        ...


class WhitespaceTokenizer:
    """Whitespace-delimited tokenizer — the pure default (no model, no deps).

    A "token" is a maximal run of non-whitespace characters. On normalized text
    this equals the single-space-delimited words, so joining a slice of tokens
    with single spaces reconstructs a valid normalized substring exactly. Token
    counts approximate (under-count vs. wordpiece), which is acceptable for a v1
    chunk-sizing heuristic; Phase 4.b can register a model-accurate tokenizer as
    a distinct profile.
    """

    id = "whitespace-1"

    def tokenize(self, text: str) -> list[str]:
        return text.split()

    def count(self, text: str) -> int:
        return len(text.split())


#: The default tokenizer instance used when a caller does not supply one.
DEFAULT_TOKENIZER = WhitespaceTokenizer()
