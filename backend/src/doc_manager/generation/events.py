"""Normalized generation stream events (TECHSTACK 5.13, contract §8.3).

Every adapter (Ollama, OpenAI, …) emits *these* events, never its SDK's own
types. The Ask service maps them to the public SSE ``ask.*`` events and to the
normal Ask result, so provider specifics never leak to clients.

A well-formed stream is: exactly one ``GenStarted`` first; zero or more
``GenDelta``; an optional ``GenUsage``; then a terminal ``GenFinished`` (or a
``GenRefusal``). Transport/provider faults surface as a raised
``GenerationError`` (see ``errors.py``), not as an event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FinishReason(StrEnum):
    #: Model stopped naturally.
    stop = "stop"
    #: Output hit the max-tokens limit.
    length = "length"
    #: Model declined to answer (valid, not an infra error).
    refusal = "refusal"
    #: Provider content filter stopped generation.
    content_filter = "content_filter"


@dataclass(frozen=True, slots=True)
class Usage:
    """Provider-reported token counts; any field may be ``None`` when absent.

    Counts are provider-reported and MUST NOT be assumed comparable across
    providers (contract §8.2).
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class GenStarted:
    """First event: the provider/model actually selected and its data boundary."""

    provider_id: str
    model_id: str
    data_boundary: str


@dataclass(frozen=True, slots=True)
class GenDelta:
    """A non-empty answer text fragment. Fragments concatenate in order."""

    text: str


@dataclass(frozen=True, slots=True)
class GenUsage:
    """Token usage, emitted at most once (usually just before finish)."""

    usage: Usage


@dataclass(frozen=True, slots=True)
class GenFinished:
    """Terminal success event with the finish reason and optional final usage."""

    reason: FinishReason
    usage: Usage | None = None


@dataclass(frozen=True, slots=True)
class GenRefusal:
    """Terminal: the model refused. A 200 result with ``status: refused``."""

    message: str


#: Any event an adapter may yield.
GenerationEvent = GenStarted | GenDelta | GenUsage | GenFinished | GenRefusal
