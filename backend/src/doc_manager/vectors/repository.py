"""Qdrant repository: collection lifecycle + idempotent point ops (TECHSTACK 5.9).

Wraps an ``AsyncQdrantClient`` for one collection (named by the active embedding
profile, so incompatible vectors never share it). Responsibilities:

* create the collection for a profile, or **validate and refuse** an existing one
  whose vector size / distance disagree with the profile;
* upsert deterministic points idempotently (re-index overwrites in place);
* delete a content object's points when its canonical content is gone / a profile
  is retired;
* search by query vector with a score threshold and optional content-object
  restriction (source/extension/tag/status filters are resolved from PostgreSQL by
  the retrieval layer and passed here as an allowed content-object set);
* expose the point set for a content object so a consistency check can compare SQL
  chunk rows against vector points.

Paths, filenames, tags, and source names are never stored or accepted here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from doc_manager.core.config import Settings
from doc_manager.embedding.profile import DISTANCE_COSINE, EmbeddingProfile
from doc_manager.vectors.errors import VectorStoreError, VectorStoreErrorCode
from doc_manager.vectors.point import VectorPoint

_DISTANCES = {
    DISTANCE_COSINE: models.Distance.COSINE,
    "dot": models.Distance.DOT,
    "euclid": models.Distance.EUCLID,
}


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieval result: score + the retrieval-only payload fields."""

    id: uuid.UUID
    score: float
    content_object_id: str
    chunk_id: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    text: str


class QdrantRepository:
    def __init__(self, client: AsyncQdrantClient, *, collection: str) -> None:
        self._client = client
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self, profile: EmbeddingProfile) -> None:
        """Create the collection if absent; validate geometry if present.

        A size/metric mismatch raises ``VectorStoreError`` rather than corrupting
        the collection (TECHSTACK 5.8/5.9).
        """
        if await self._client.collection_exists(self._collection):
            info = await self._client.get_collection(self._collection)
            vectors = info.config.params.vectors
            if not isinstance(vectors, models.VectorParams):
                raise VectorStoreError(
                    VectorStoreErrorCode.collection_mismatch,
                    f"collection {self._collection} uses named vectors; "
                    "expected a single unnamed vector",
                )
            size = vectors.size
            distance = vectors.distance.value.lower()
            if not profile.is_compatible_with(vector_size=size, distance=distance):
                raise VectorStoreError(
                    VectorStoreErrorCode.collection_mismatch,
                    f"collection {self._collection} is {size}-d/{distance}, "
                    f"incompatible with profile {profile.vector_size}-d/{profile.distance}",
                )
            return
        await self._client.create_collection(
            self._collection,
            vectors_config=models.VectorParams(
                size=profile.vector_size, distance=self._distance(profile.distance)
            ),
        )

    async def upsert_points(self, points: Sequence[VectorPoint]) -> int:
        """Idempotently upsert points; returns the number written."""
        if not points:
            return 0
        await self._client.upsert(
            self._collection,
            points=[
                models.PointStruct(id=str(p.id), vector=p.vector, payload=p.payload) for p in points
            ],
        )
        return len(points)

    async def delete_for_content(self, content_object_id: uuid.UUID | str) -> None:
        """Remove every point belonging to one content object."""
        await self._client.delete(
            self._collection,
            points_selector=models.FilterSelector(filter=_content_filter([str(content_object_id)])),
        )

    async def list_collection_names(self) -> list[str]:
        """All collection names known to this Qdrant instance."""
        response = await self._client.get_collections()
        return [c.name for c in response.collections]

    async def drop_collection(self, name: str) -> None:
        """Delete a whole collection (used to retire a superseded embedding profile)."""
        await self._client.delete_collection(name)

    async def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int,
        score_threshold: float | None = None,
        content_object_ids: Sequence[uuid.UUID | str] | None = None,
    ) -> list[SearchHit]:
        """Nearest points to ``vector``, optionally restricted to content objects.

        ``content_object_ids`` is the allow-set the retrieval layer derives from
        SQL source/extension/tag/status filters (empty set → no results).
        """
        query_filter = None
        if content_object_ids is not None:
            if len(content_object_ids) == 0:
                return []
            query_filter = _content_filter([str(c) for c in content_object_ids])
        response = await self._client.query_points(
            self._collection,
            query=list(vector),
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
            with_payload=True,
        )
        return [_hit(point) for point in response.points]

    async def count_for_content(self, content_object_id: uuid.UUID | str) -> int:
        result = await self._client.count(
            self._collection,
            count_filter=_content_filter([str(content_object_id)]),
        )
        return result.count

    async def point_ids_for_content(
        self,
        content_object_id: uuid.UUID | str,
        *,
        embedding_profile_hash: str | None = None,
    ) -> set[str]:
        """All point IDs for a content object (for SQL↔vector consistency checks).

        Optionally restrict to one embedding profile (a content object may hold
        points under several). Returns empty if the collection does not exist yet.
        """
        if not await self._client.collection_exists(self._collection):
            return set()
        scroll_filter = _content_filter(
            [str(content_object_id)], embedding_profile_hash=embedding_profile_hash
        )
        ids: set[str] = set()
        offset: uuid.UUID | int | str | None = None
        while True:
            points, offset = await self._client.scroll(
                self._collection,
                scroll_filter=scroll_filter,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids.update(str(p.id) for p in points)
            if offset is None:
                break
        return ids

    @staticmethod
    def _distance(distance: str) -> models.Distance:
        try:
            return _DISTANCES[distance]
        except KeyError as exc:
            raise VectorStoreError(
                VectorStoreErrorCode.collection_mismatch, f"unsupported distance: {distance}"
            ) from exc


def _content_filter(
    content_object_ids: list[str], *, embedding_profile_hash: str | None = None
) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(
            key="content_object_id", match=models.MatchAny(any=content_object_ids)
        )
    ]
    if embedding_profile_hash is not None:
        must.append(
            models.FieldCondition(
                key="embedding_profile_hash",
                match=models.MatchValue(value=embedding_profile_hash),
            )
        )
    return models.Filter(must=must)


def _hit(point: models.ScoredPoint) -> SearchHit:
    payload = point.payload or {}
    return SearchHit(
        id=uuid.UUID(str(point.id)),
        score=point.score,
        content_object_id=str(payload.get("content_object_id")),
        chunk_id=str(payload.get("chunk_id")),
        chunk_index=int(payload.get("chunk_index", 0)),
        page_start=payload.get("page_start"),
        page_end=payload.get("page_end"),
        text=str(payload.get("text", "")),
    )


def build_qdrant_repository(settings: Settings, profile: EmbeddingProfile) -> QdrantRepository:
    """Construct a repository for the profile's collection against ``qdrant_url``."""
    client = AsyncQdrantClient(url=settings.qdrant_url)
    collection = profile.collection_name(settings.qdrant_collection)
    return QdrantRepository(client, collection=collection)
