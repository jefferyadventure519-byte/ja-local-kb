"""Incremental indexing orchestration with a fail-closed freshness gate."""

from __future__ import annotations

import hashlib

from .embedding import Embedder
from .errors import (
    FreshnessError,
    IdentityConflictError,
    IndexIntegrityError,
    KnowledgeBaseError,
    NotIndexedError,
    SourceMissingError,
)
from .index_store import LanceIndex
from .locking import RuntimeLock
from .models import AppSettings, ParsedChunk, SourceSpec, SourceStatus
from .parser import parse_source
from .registry import (
    load_registry,
    resolve_source_path,
)
from .registry import (
    remove_source as remove_registered_source,
)
from .reranker import Reranker
from .retrieval import Retriever, SearchMode
from .state import StateStore


class KnowledgeService:
    def __init__(
        self,
        settings: AppSettings,
        embedder: Embedder,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.registry = load_registry(settings.source_registry)
        self.lock = RuntimeLock(settings.runtime_root)
        self.state = StateStore(settings.resolved_state_path)
        self.index = LanceIndex(settings.resolved_lancedb_root)
        self.retriever = Retriever(
            self.index,
            self.state,
            embedder,
            settings.retrieval,
            reranker,
        )
        self.state.register(self.registry)
        try:
            self.state.bind_embedding_fingerprint(embedder.fingerprint)
        except ValueError as exc:
            raise IdentityConflictError(str(exc)) from exc
        self._loaded_index_version = self.state.index_version()

    def sync_all(self) -> list[dict]:
        results = []
        for source in self.registry.sources:
            if source.enabled:
                results.append(self.sync_source(source.source_id))
        return results

    def sync_source(self, source_id: str) -> dict:
        with self.lock.exclusive():
            self._refresh_index_view_locked()
            return self._sync_source_locked(source_id)

    def remove_source(self, source_id: str) -> dict:
        with self.lock.exclusive():
            self._refresh_index_view_locked()
            self._source(source_id)
            removed = remove_registered_source(
                self.settings.source_registry,
                source_id,
            )
            self.registry = load_registry(self.settings.source_registry)
            deleted_index_chunks = self.index.delete_source(source_id)
            deleted_state = self.state.delete_source(source_id)
            self._loaded_index_version = deleted_state["index_version"]
            return {
                "action": "removed",
                "source": removed.model_dump(mode="json"),
                "deleted_index_chunks": deleted_index_chunks,
                "deleted_state_chunks": deleted_state["chunk_count"],
                "index_version": deleted_state["index_version"],
            }

    def _sync_source_locked(self, source_id: str) -> dict:
        source = self._source(source_id)
        if not source.enabled:
            self.state.set_status(source_id, SourceStatus.DISABLED)
            return {"source_id": source_id, "status": SourceStatus.DISABLED.value}
        try:
            path = resolve_source_path(self.settings.vault_root, source)
            observed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.state.set_status(
                source_id,
                SourceStatus.SYNCING,
                observed_hash=observed_hash,
            )
            chunks = parse_source(path, source, self.settings.chunking)
            if not chunks:
                raise KnowledgeBaseError(
                    "Source produced no searchable chunks",
                    details={"source_id": source_id},
                )
            existing = self.index.rows_for_source(source_id)
            vectors, embedded_count, reused_count = self._vectors_for(
                chunks,
                existing,
            )
            rows = [
                self.index.make_row(chunk, vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            dimension = len(vectors[0])
            self.state.validate_vector_dimension(dimension)
            self.index.replace_source(source_id, rows)
            version = self.state.commit_source(
                source,
                chunks,
                vector_dimension=dimension,
            )
            self._loaded_index_version = version
            return {
                "source_id": source_id,
                "status": SourceStatus.FRESH.value,
                "chunk_count": len(chunks),
                "embedded_count": embedded_count,
                "reused_count": reused_count,
                "vector_dimension": dimension,
                "index_version": version,
            }
        except SourceMissingError as exc:
            self.state.set_status(
                source_id,
                SourceStatus.MISSING,
                error_code=exc.code,
                error_message=str(exc),
            )
            raise
        except IdentityConflictError as exc:
            self.state.set_status(
                source_id,
                SourceStatus.IDENTITY_CONFLICT,
                error_code=exc.code,
                error_message=str(exc),
            )
            raise
        except Exception as exc:
            code = getattr(exc, "code", "sync_failed")
            self.state.set_status(
                source_id,
                SourceStatus.FAILED,
                error_code=code,
                error_message=str(exc),
            )
            raise

    def detect_drift(self) -> list[dict]:
        drift: list[dict] = []
        for source in self.registry.sources:
            if not source.enabled:
                continue
            row = self.state.get_source(source.source_id)
            try:
                path = resolve_source_path(self.settings.vault_root, source)
            except SourceMissingError as exc:
                self.state.set_status(
                    source.source_id,
                    SourceStatus.MISSING,
                    error_code=exc.code,
                    error_message=str(exc),
                )
                drift.append(
                    {
                        "source_id": source.source_id,
                        "status": SourceStatus.MISSING.value,
                    }
                )
                continue
            observed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            committed_hash = row["committed_hash"] if row else ""
            current_status = row["status"] if row else SourceStatus.NOT_INDEXED.value
            if observed_hash != committed_hash:
                if (
                    current_status
                    in {
                        SourceStatus.NOT_INDEXED.value,
                        SourceStatus.STALE.value,
                    }
                    and not committed_hash
                ):
                    self.state.set_status(
                        source.source_id,
                        SourceStatus.NOT_INDEXED,
                        observed_hash=observed_hash,
                    )
                    current_status = SourceStatus.NOT_INDEXED.value
                    drift.append(
                        {
                            "source_id": source.source_id,
                            "status": current_status,
                        }
                    )
                    continue
                preserved = {
                    SourceStatus.FAILED.value,
                    SourceStatus.IDENTITY_CONFLICT.value,
                    SourceStatus.SYNCING.value,
                }
                if current_status not in preserved:
                    self.state.set_status(
                        source.source_id,
                        SourceStatus.STALE,
                        observed_hash=observed_hash,
                    )
                    current_status = SourceStatus.STALE.value
            elif committed_hash and current_status == SourceStatus.STALE.value:
                self.state.set_status(
                    source.source_id,
                    SourceStatus.FRESH,
                    observed_hash=observed_hash,
                )
                current_status = SourceStatus.FRESH.value
            if current_status != SourceStatus.FRESH.value:
                drift.append(
                    {
                        "source_id": source.source_id,
                        "status": current_status,
                    }
                )
        return drift

    def assert_ready(self) -> dict:
        self.detect_drift()
        payload = self._status_payload()
        if payload["enabled_source_count"] == 0:
            raise NotIndexedError("No enabled knowledge sources are registered")
        if not payload["ready"]:
            raise FreshnessError(
                "Knowledge retrieval is blocked until every enabled source is fresh",
                details={
                    "blocking_sources": payload["blocking_sources"],
                    "index_version": payload["index_version"],
                },
            )
        return payload

    def status(self, *, verify_files: bool = True) -> dict:
        if verify_files:
            self.detect_drift()
        return self._status_payload()

    def search(
        self,
        query: str,
        *,
        mode: SearchMode = "recall",
        top_k: int | None = None,
        project_ids: list[str] | None = None,
        client_ids: list[str] | None = None,
        include_candidates: bool = False,
    ) -> dict:
        with self.lock.exclusive():
            self.assert_ready()
            self._refresh_index_view_locked()
            return self.retriever.search(
                query,
                mode=mode,
                top_k=top_k,
                project_ids=project_ids,
                client_ids=client_ids,
                include_candidates=include_candidates,
                source_ids=[
                    source.source_id
                    for source in self.registry.sources
                    if source.enabled
                ],
            )

    def get_source(
        self,
        *,
        source_id: str | None = None,
        evidence_id: str | None = None,
        max_chars: int = 12000,
    ) -> dict:
        with self.lock.exclusive():
            self.assert_ready()
            self._refresh_index_view_locked()
            if max_chars < 100 or max_chars > 100000:
                raise ValueError("max_chars must be between 100 and 100000")
            if bool(source_id) == bool(evidence_id):
                raise ValueError("Provide exactly one of source_id or evidence_id")
            if evidence_id:
                if not evidence_id.startswith("ev_"):
                    raise ValueError("Invalid evidence_id")
                row = self.index.get_chunk(evidence_id[3:])
                enabled_source_ids = {
                    source.source_id
                    for source in self.registry.sources
                    if source.enabled
                }
                if row is None or row["source_id"] not in enabled_source_ids:
                    raise KeyError(f"Unknown evidence_id: {evidence_id}")
                state_source = self.state.get_source(row["source_id"])
                if (
                    state_source is None
                    or not state_source["committed_hash"]
                    or row["document_hash"] != state_source["committed_hash"]
                ):
                    raise IndexIntegrityError(
                        "Evidence does not belong to the committed source version",
                        details={
                            "evidence_id": evidence_id,
                            "source_id": row["source_id"],
                            "index_version": self.state.index_version(),
                        },
                    )
                return {
                    "kind": "evidence",
                    "evidence_id": evidence_id,
                    "source_id": row["source_id"],
                    "project_id": row["project_id"],
                    "project_name": row["project_name"],
                    "client_id": row["client_id"],
                    "document_role": row["document_role"],
                    "relative_path": row["relative_path"],
                    "heading": row["heading"],
                    "source_text": row["source_text"][:max_chars],
                    "truncated": len(row["source_text"]) > max_chars,
                    "index_version": self.state.index_version(),
                    "freshness": "fresh",
                }
            source = self._source(source_id or "")
            if not source.enabled:
                raise KeyError(f"Unknown source_id: {source.source_id}")
            path = resolve_source_path(self.settings.vault_root, source)
            content = path.read_text(encoding="utf-8-sig")
            return {
                "kind": "source",
                "source_id": source.source_id,
                "project_id": source.project_id,
                "project_name": source.project_name,
                "client_id": source.client_id,
                "document_role": source.document_role,
                "relative_path": source.relative_path,
                "content": content[:max_chars],
                "truncated": len(content) > max_chars,
                "index_version": self.state.index_version(),
                "freshness": "fresh",
            }

    def _source(self, source_id: str) -> SourceSpec:
        for source in self.registry.sources:
            if source.source_id == source_id:
                return source
        raise KeyError(f"Unknown source_id: {source_id}")

    def _refresh_index_view_locked(self) -> None:
        current_version = self.state.index_version()
        if current_version == self._loaded_index_version:
            return
        self.index.refresh_from_disk()
        self._loaded_index_version = current_version

    def _vectors_for(
        self,
        chunks: list[ParsedChunk],
        existing: dict[str, dict],
    ) -> tuple[list[list[float]], int, int]:
        vectors: list[list[float] | None] = [None] * len(chunks)
        to_embed: list[str] = []
        positions: list[int] = []
        reused = 0
        for position, chunk in enumerate(chunks):
            row = existing.get(chunk.chunk_id)
            if row and row["embedding_hash"] == chunk.embedding_hash:
                vectors[position] = [float(value) for value in row["vector"]]
                reused += 1
            else:
                positions.append(position)
                to_embed.append(chunk.retrieval_text)
        embedded = self.embedder.embed(to_embed)
        if len(embedded) != len(positions):
            raise ValueError("Embedder returned an unexpected vector count")
        for position, vector in zip(positions, embedded, strict=True):
            vectors[position] = vector
        finalized = [vector for vector in vectors if vector is not None]
        if len(finalized) != len(chunks):
            raise ValueError("Missing vector after incremental embedding")
        dimensions = {len(vector) for vector in finalized}
        if len(dimensions) != 1:
            raise ValueError("Mixed vector dimensions are forbidden")
        return finalized, len(embedded), reused

    def _status_payload(self) -> dict:
        return self.state.status_payload(
            {source.source_id for source in self.registry.sources}
        )
