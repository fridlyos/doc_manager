"""Unit coverage for Phase 2 deterministic helpers."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from doc_manager.api.envelope import iso_utc
from doc_manager.api.errors import Problem
from doc_manager.api.pagination import CURSOR_TTL_SECONDS, decode_cursor, encode_cursor
from doc_manager.api.v1.routes.locations import _roots_overlap, _validate_scan_root
from doc_manager.core.config import Settings
from doc_manager.domain.enums import PathStyle
from doc_manager.jobs.queue import compute_retry_delay


def test_equal_jitter_backoff_is_seeded_and_bounded() -> None:
    rng = random.Random(42)
    delays = [
        compute_retry_delay(
            attempt,
            base_delay_seconds=4,
            max_delay_seconds=10,
            rng=rng,
        )
        for attempt in (1, 2, 3, 4)
    ]

    assert delays == pytest.approx(
        [3.2788535969157673, 4.100043020890668, 6.3751465918455965, 6.116053690744113]
    )
    assert 2 <= delays[0] <= 4
    assert 4 <= delays[1] <= 8
    assert all(5 <= delay <= 10 for delay in delays[2:])


def test_cursor_round_trip_binding_tamper_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(database_url="postgresql+psycopg://test:test@db/test")
    monkeypatch.setattr("doc_manager.api.pagination.time.time", lambda: 1_000_000)
    token = encode_cursor(
        settings,
        route="/api/v1/jobs",
        sort="-requested_at",
        filters={"status": ["queued"]},
        last_key=["2026-01-02T03:04:05Z", "00000000-0000-4000-8000-000000000001"],
    )

    assert decode_cursor(
        settings,
        token,
        route="/api/v1/jobs",
        sort="-requested_at",
        filters={"status": ["queued"]},
    ) == ["2026-01-02T03:04:05Z", "00000000-0000-4000-8000-000000000001"]

    with pytest.raises(Problem, match="not valid") as wrong_route:
        decode_cursor(
            settings,
            token,
            route="/api/v1/locations",
            sort="-requested_at",
            filters={"status": ["queued"]},
        )
    assert wrong_route.value.code == "invalid_cursor"

    encoded, mac = token.rsplit(".", 1)
    with pytest.raises(Problem) as tampered:
        decode_cursor(
            settings,
            f"{encoded}.{mac[:-1]}0",
            route="/api/v1/jobs",
            sort="-requested_at",
            filters={"status": ["queued"]},
        )
    assert tampered.value.code == "invalid_cursor"

    monkeypatch.setattr(
        "doc_manager.api.pagination.time.time", lambda: 1_000_000 + CURSOR_TTL_SECONDS + 1
    )
    with pytest.raises(Problem) as expired:
        decode_cursor(
            settings,
            token,
            route="/api/v1/jobs",
            sort="-requested_at",
            filters={"status": ["queued"]},
        )
    assert expired.value.code == "cursor_expired"
    assert expired.value.retryable is True


def _settings(allowed: str) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@db/test",
        allowed_source_roots=allowed,
    )


def test_validate_scan_root_accepts_windows_styles_on_any_host() -> None:
    settings = _settings("Z:\\,C:\\Docs,\\\\nas01\\documents,/sources")

    mapped = _validate_scan_root(settings, "Z:\\Projects\\Reports", PathStyle.mapped_drive)
    assert str(mapped) == "Z:\\Projects\\Reports"
    # Windows semantics are case-insensitive, including the allowed-root check.
    _validate_scan_root(settings, "z:\\projects", PathStyle.mapped_drive)
    # Forward slashes normalize to backslashes under windows styles.
    assert str(_validate_scan_root(settings, "C:/Docs/legal", PathStyle.windows)) == (
        "C:\\Docs\\legal"
    )
    unc = _validate_scan_root(settings, "\\\\nas01\\documents\\archive", PathStyle.unc)
    assert str(unc) == "\\\\nas01\\documents\\archive"
    _validate_scan_root(settings, "/sources/docs", PathStyle.linux)


def test_validate_scan_root_rejects_bad_shapes() -> None:
    settings = _settings("Z:\\,/sources")

    with pytest.raises(Problem, match="absolute"):
        _validate_scan_root(settings, "Docs\\Reports", PathStyle.mapped_drive)
    with pytest.raises(Problem, match="absolute"):
        # Rooted but drive-less is not absolute under Windows semantics.
        _validate_scan_root(settings, "\\Docs", PathStyle.windows)
    with pytest.raises(Problem, match="drive letter"):
        # UNC shape under a drive-letter style points at the wrong path_style.
        _validate_scan_root(settings, "\\\\nas01\\documents", PathStyle.windows)
    with pytest.raises(Problem, match="\\\\\\\\server"):
        _validate_scan_root(settings, "Z:\\Docs", PathStyle.unc)
    with pytest.raises(Problem, match="'\\.\\.'"):
        _validate_scan_root(settings, "Z:\\Docs\\..\\secrets", PathStyle.mapped_drive)
    with pytest.raises(Problem, match="allowed source root"):
        _validate_scan_root(settings, "D:\\Elsewhere", PathStyle.mapped_drive)
    with pytest.raises(Problem, match="allowed source root"):
        # A posix-style path never satisfies a windows-only allowed list and vice versa.
        _validate_scan_root(_settings("Z:\\"), "/sources/docs", PathStyle.linux)


def test_roots_overlap_is_style_aware() -> None:
    settings = _settings("Z:\\,/sources")
    candidate = _validate_scan_root(settings, "Z:\\Docs\\Sub", PathStyle.mapped_drive)

    assert _roots_overlap(candidate, "z:\\docs", PathStyle.mapped_drive.value)
    assert _roots_overlap(candidate, "Z:\\Docs\\Sub\\Deeper", PathStyle.windows.value)
    assert not _roots_overlap(candidate, "Z:\\Other", PathStyle.mapped_drive.value)
    # Posix and windows roots never overlap each other.
    posix = _validate_scan_root(settings, "/sources/docs", PathStyle.linux)
    assert not _roots_overlap(posix, "Z:\\Docs", PathStyle.mapped_drive.value)


def test_iso_utc_normalizes_offsets_and_fractional_seconds() -> None:
    assert iso_utc(None) is None
    assert iso_utc(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05Z"
    assert iso_utc(datetime(2026, 1, 2, 3, 4, 5, 120_000, tzinfo=UTC)) == "2026-01-02T03:04:05.12Z"
    eastern = timezone(-timedelta(hours=5))
    assert iso_utc(datetime(2026, 1, 1, 22, 4, 5, tzinfo=eastern)) == "2026-01-02T03:04:05Z"
