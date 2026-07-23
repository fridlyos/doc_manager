"""Content-addressed, compressed artifact storage: atomicity, reuse, round-trip."""

from __future__ import annotations

from pathlib import Path

from doc_manager.artifact_store import ArtifactStore, content_address
from doc_manager.extraction.base import ExtractedDocument, ExtractedPage
from doc_manager.extraction.normalize import normalize
from doc_manager.extraction.profile import extraction_profile_hash

_PROFILE = extraction_profile_hash("pdf", "pdf-1")


def _normalized(pages: list[tuple[int | None, str]]):
    doc = ExtractedDocument(
        extractor_name="pdf",
        extractor_version="pdf-1",
        pages=[ExtractedPage(index=i, page_number=pn, text=t) for i, (pn, t) in enumerate(pages)],
    )
    return normalize(doc)


def _store(store: ArtifactStore, normalized) -> object:
    return store.store(
        normalized,
        extractor_name="pdf",
        extractor_version="pdf-1",
        extraction_profile_hash=_PROFILE,
        metadata={"page_count": normalized.character_count},
    )


def test_content_address_is_stable_and_sharded() -> None:
    addr = content_address("abcd1234", _PROFILE, "norm-1")
    assert addr.startswith("norm-1/ab/abcd1234.")
    assert addr.endswith(".json.gz")
    # Different structure hash -> different address.
    assert addr != content_address("ffff0000", _PROFILE, "norm-1")


def test_store_writes_and_round_trips(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    normalized = _normalized([(1, "Page one text"), (2, "Page two text")])
    result = _store(store, normalized)

    assert result.reused is False
    assert (tmp_path / result.relative_path).exists()
    pages = store.load_pages(result.relative_path)
    assert [p.page_number for p in pages] == [1, 2]
    assert pages[0].text == "Page one text"
    loaded = store.load(result.relative_path)
    assert loaded["structure_hash"] == normalized.structure_hash
    assert loaded["text_hash"] == normalized.text_hash


def test_store_is_idempotent_and_reuses(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    normalized = _normalized([(1, "same content")])
    first = _store(store, normalized)
    second = _store(store, normalized)
    assert first.reused is False
    assert second.reused is True
    assert first.relative_path == second.relative_path


def test_store_leaves_no_temp_files(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    _store(store, _normalized([(1, "text")]))
    assert list(tmp_path.rglob("*.tmp")) == []
    # Sanity: exactly one artifact was written.
    assert len(list(tmp_path.rglob("*.json.gz"))) == 1
