from __future__ import annotations

from doc_manager.health import Component, ComponentStatus, ReadinessReport


def _report(**statuses: ComponentStatus) -> ReadinessReport:
    required = {"postgres", "qdrant"}
    return ReadinessReport(
        components=[
            Component(name=n, required=n in required, status=s) for n, s in statuses.items()
        ]
    )


def test_ready_when_required_up() -> None:
    report = _report(
        postgres=ComponentStatus.up,
        qdrant=ComponentStatus.up,
        ollama=ComponentStatus.down,
    )
    assert report.ready


def test_not_ready_when_required_down() -> None:
    report = _report(postgres=ComponentStatus.down, qdrant=ComponentStatus.up)
    assert not report.ready


def test_search_only_when_no_provider_up() -> None:
    report = _report(
        postgres=ComponentStatus.up,
        qdrant=ComponentStatus.up,
        ollama=ComponentStatus.down,
        openai=ComponentStatus.disabled,
    )
    assert report.ready
    assert report.search_only


def test_not_search_only_when_provider_up() -> None:
    report = _report(
        postgres=ComponentStatus.up,
        qdrant=ComponentStatus.up,
        ollama=ComponentStatus.up,
    )
    assert not report.search_only
