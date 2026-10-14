"""File-name index: one vector per catalog entry, embedded from its path.

Most files are named for what they are ("fridge from 2018 - 4 years warranty"),
often more plainly than their (possibly OCR-noisy) text. Retrieval searches this
index first so a strong name match puts that file's passages at the top.

The collection lives beside the chunk collection and is bound to the same
embedding profile. Like the chunk payload it stores **no paths or names** — only
ids and a hash of the embedded name text (so a rename is detectable without
keeping the name). Whether an entry is still indexed, and its current content,
are always resolved from PostgreSQL at query time.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from doc_manager.core.config import Settings
from doc_manager.embedding.profile import EmbeddingProfile
from doc_manager.vectors.errors import VectorStoreError, VectorStoreErrorCode
from doc_manager.vectors.repository import QdrantRepository

#: Sync-client conflict suffixes carry no meaning about the document, e.g.
#: "_DiskStation_Dec-10-1230-2022_DownloadConflict" (Synology Drive).
_CONFLICT_SUFFIX_RE = re.compile(
    r"_[^_/\\]+_[A-Za-z]{3}-\d{1,2}-\d{4}-\d{4}_[A-Za-z]*Conflict", re.IGNORECASE
)
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SEPARATORS_RE = re.compile(r"[._\\-]+")
_SPACES_RE = re.compile(r"\s+")


def file_name_text(relative_path: str) -> str:
    """Embedding input for a file's name: folders + name, as plain words.

    "2615 Documents/Appliences Receipts and Warranties/fridge from 2018  - 4 years
    warranty_DiskStation_Dec-10-1230-2022_DownloadConflict.pdf" becomes
    "2615 Documents / Appliences Receipts and Warranties / fridge from 2018 4 years
    warranty". The file name is the most specific part, so it is also repeated
    first.
    """
    path = relative_path.replace("\\", "/").strip("/")
    path = _EXTENSION_RE.sub("", path)
    path = _CONFLICT_SUFFIX_RE.sub("", path)
    parts = [_SPACES_RE.sub(" ", _SEPARATORS_RE.sub(" ", p)).strip() for p in path.split("/")]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return f"{parts[-1]} ({' / '.join(parts)})"


def name_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NamePoint:
    catalog_entry_id: str
    content_object_id: str
    source_location_id: str
    name_hash: str
    vector: list[float]


@dataclass(frozen=True, slots=True)
class NameHit:
    catalog_entry_id: str
    score: float


class NameIndex:
    def __init__(self, client: AsyncQdrantClient, *, collection: str) -> None:
        self._client = client
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self, profile: EmbeddingProfile) -> None:
        if await self._client.collection_exists(self._collection):
            info = await self._client.get_collection(self._collection)
            vectors = info.config.params.vectors
            if not isinstance(vectors, models.VectorParams) or not profile.is_compatible_with(
                vector_size=vectors.size, distance=vectors.distance.value.lower()
            ):
                raise VectorStoreError(
                    VectorStoreErrorCode.collection_mismatch,
                    f"name collection {self._collection} is incompatible with the profile",
                )
            return
        await self._client.create_collection(
            self._collection,
            vectors_config=models.VectorParams(
                size=profile.vector_size,
                distance=QdrantRepository._distance(profile.distance),
            ),
        )
        # Lets a location's points be listed/pruned without a full scroll.
        with contextlib.suppress(Exception):
            await self._client.create_payload_index(
                self._collection,
                field_name="source_location_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, points: Sequence[NamePoint]) -> int:
        if not points:
            return 0
        await self._client.upsert(
            self._collection,
            points=[
                models.PointStruct(
                    id=p.catalog_entry_id,
                    vector=p.vector,
                    payload={
                        "catalog_entry_id": p.catalog_entry_id,
                        "content_object_id": p.content_object_id,
                        "source_location_id": p.source_location_id,
                        "name_hash": p.name_hash,
                    },
                )
                for p in points
            ],
        )
        return len(points)

    async def delete(self, catalog_entry_ids: Sequence[str]) -> None:
        if not catalog_entry_ids:
            return
        await self._client.delete(
            self._collection,
            points_selector=models.PointIdsList(points=[str(i) for i in catalog_entry_ids]),
        )

    async def hashes_for_location(self, source_location_id: str) -> dict[str, str]:
        """``catalog_entry_id -> name_hash`` for every point of one location."""
        if not await self._client.collection_exists(self._collection):
            return {}
        found: dict[str, str] = {}
        offset: uuid.UUID | int | str | None = None
        location_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="source_location_id", match=models.MatchValue(value=source_location_id)
                )
            ]
        )
        while True:
            points, offset = await self._client.scroll(
                self._collection,
                scroll_filter=location_filter,
                limit=256,
                offset=offset,
                with_payload=["name_hash"],
                with_vectors=False,
            )
            for p in points:
                found[str(p.id)] = str((p.payload or {}).get("name_hash", ""))
            if offset is None:
                break
        return found

    async def search(self, vector: Sequence[float], *, top_k: int) -> list[NameHit]:
        if not await self._client.collection_exists(self._collection):
            return []
        response = await self._client.query_points(
            self._collection, query=list(vector), limit=top_k, with_payload=False
        )
        return [NameHit(catalog_entry_id=str(p.id), score=p.score) for p in response.points]


def name_collection(settings: Settings, profile: EmbeddingProfile) -> str:
    return profile.collection_name(settings.qdrant_names_collection)


def build_name_index(settings: Settings, profile: EmbeddingProfile) -> NameIndex:
    client = AsyncQdrantClient(url=settings.qdrant_url)
    return NameIndex(client, collection=name_collection(settings, profile))
