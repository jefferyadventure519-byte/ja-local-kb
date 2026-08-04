"""SQLite source-of-state ledger for freshness and incremental commits."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import ParsedChunk, SourceRegistry, SourceSpec, SourceStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    document_role TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    observed_hash TEXT NOT NULL DEFAULT '',
                    committed_hash TEXT NOT NULL DEFAULT '',
                    body_hash TEXT NOT NULL DEFAULT '',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    indexed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    embedding_hash TEXT NOT NULL,
                    vector_dimension INTEGER NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES sources(source_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_source
                    ON chunks(source_id);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('index_version', '0')"
            )

    def register(self, registry: SourceRegistry) -> None:
        now = utc_now()
        registered_ids = {source.source_id for source in registry.sources}
        with self.connect() as connection:
            for source in registry.sources:
                existing = connection.execute(
                    "SELECT relative_path, enabled, status FROM sources "
                    "WHERE source_id = ?",
                    (source.source_id,),
                ).fetchone()
                status = (
                    SourceStatus.DISABLED
                    if not source.enabled
                    else SourceStatus.NOT_INDEXED
                )
                if existing is not None:
                    if not source.enabled:
                        status = SourceStatus.DISABLED
                    elif (
                        not existing["enabled"]
                        or existing["relative_path"] != source.relative_path
                    ):
                        status = SourceStatus.STALE
                    else:
                        status = SourceStatus(existing["status"])
                connection.execute(
                    """
                    INSERT INTO sources(
                        source_id, project_id, project_name, client_id,
                        document_role, relative_path, enabled, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        project_id=excluded.project_id,
                        project_name=excluded.project_name,
                        client_id=excluded.client_id,
                        document_role=excluded.document_role,
                        relative_path=excluded.relative_path,
                        enabled=excluded.enabled,
                        status=excluded.status,
                        updated_at=excluded.updated_at
                    """,
                    (
                        source.source_id,
                        source.project_id,
                        source.project_name,
                        source.client_id,
                        source.document_role,
                        source.relative_path,
                        int(source.enabled),
                        status.value,
                        now,
                    ),
                )
            if registered_ids:
                placeholders = ",".join("?" for _ in registered_ids)
                connection.execute(
                    f"UPDATE sources SET enabled=0, status=?, updated_at=? "
                    f"WHERE source_id NOT IN ({placeholders})",
                    (
                        SourceStatus.DISABLED.value,
                        now,
                        *sorted(registered_ids),
                    ),
                )
            else:
                connection.execute(
                    "UPDATE sources SET enabled=0, status=?, updated_at=?",
                    (SourceStatus.DISABLED.value, now),
                )

    def set_status(
        self,
        source_id: str,
        status: SourceStatus,
        *,
        observed_hash: str | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        assignments = [
            "status = ?",
            "error_code = ?",
            "error_message = ?",
            "updated_at = ?",
        ]
        values: list[object] = [
            status.value,
            error_code,
            error_message,
            utc_now(),
        ]
        if observed_hash is not None:
            assignments.append("observed_hash = ?")
            values.append(observed_hash)
        values.append(source_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE sources SET {', '.join(assignments)} WHERE source_id = ?",
                values,
            )

    def commit_source(
        self,
        source: SourceSpec,
        chunks: Sequence[ParsedChunk],
        *,
        vector_dimension: int,
    ) -> int:
        now = utc_now()
        document_hash = chunks[0].document_hash
        body_hash = chunks[0].body_hash
        with self.connect() as connection:
            current_version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key='index_version'"
                ).fetchone()["value"]
            )
            new_version = current_version + 1
            connection.execute(
                "UPDATE meta SET value=? WHERE key='index_version'",
                (str(new_version),),
            )
            connection.execute(
                "DELETE FROM chunks WHERE source_id = ?",
                (source.source_id,),
            )
            connection.executemany(
                """
                INSERT INTO chunks(
                    chunk_id, source_id, embedding_hash, vector_dimension
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        source.source_id,
                        chunk.embedding_hash,
                        vector_dimension,
                    )
                    for chunk in chunks
                ],
            )
            connection.execute(
                """
                UPDATE sources SET
                    status=?, observed_hash=?, committed_hash=?, body_hash=?,
                    chunk_count=?, error_code='', error_message='',
                    updated_at=?, indexed_at=?
                WHERE source_id=?
                """,
                (
                    SourceStatus.FRESH.value,
                    document_hash,
                    document_hash,
                    body_hash,
                    len(chunks),
                    now,
                    now,
                    source.source_id,
                ),
            )
        return new_version

    def get_source(self, source_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_source(self, source_id: str) -> dict:
        with self.connect() as connection:
            source = connection.execute(
                "SELECT source_id FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise KeyError(f"Unknown source_id: {source_id}")
            chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM chunks WHERE source_id = ?",
                    (source_id,),
                ).fetchone()["count"]
            )
            current_version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key='index_version'"
                ).fetchone()["value"]
            )
            new_version = current_version + 1
            connection.execute(
                "UPDATE meta SET value=? WHERE key='index_version'",
                (str(new_version),),
            )
            connection.execute(
                "DELETE FROM sources WHERE source_id = ?",
                (source_id,),
            )
        return {
            "source_id": source_id,
            "chunk_count": chunk_count,
            "index_version": new_version,
        }

    def list_sources(self, source_ids: set[str] | None = None) -> list[dict]:
        with self.connect() as connection:
            if source_ids is None:
                rows = connection.execute(
                    "SELECT * FROM sources ORDER BY project_name, document_role"
                ).fetchall()
            elif source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                rows = connection.execute(
                    "SELECT * FROM sources "
                    f"WHERE source_id IN ({placeholders}) "
                    "ORDER BY project_name, document_role",
                    sorted(source_ids),
                ).fetchall()
            else:
                rows = []
        return [dict(row) for row in rows]

    def chunk_hashes(self, source_id: str) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT chunk_id, embedding_hash FROM chunks WHERE source_id = ?",
                (source_id,),
            ).fetchall()
        return {row["chunk_id"]: row["embedding_hash"] for row in rows}

    def index_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='index_version'"
            ).fetchone()
        return int(row["value"])

    def set_index_version(self, version: int) -> None:
        if version < 0:
            raise ValueError("index version cannot be negative")
        with self.connect() as connection:
            connection.execute(
                "UPDATE meta SET value=? WHERE key='index_version'",
                (str(version),),
            )

    def embedding_fingerprint(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='embedding_fingerprint'"
            ).fetchone()
        return row["value"] if row else ""

    def bind_embedding_fingerprint(self, fingerprint: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='embedding_fingerprint'"
            ).fetchone()
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key='index_version'"
                ).fetchone()["value"]
            )
            if row is not None and row["value"] != fingerprint and version > 0:
                raise ValueError(
                    "Configured embedding vector space differs from the committed index"
                )
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('embedding_fingerprint', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (fingerprint,),
            )

    def validate_vector_dimension(self, dimension: int) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='vector_dimension'"
            ).fetchone()
            version = int(
                connection.execute(
                    "SELECT value FROM meta WHERE key='index_version'"
                ).fetchone()["value"]
            )
            if row is not None and int(row["value"]) != dimension and version > 0:
                raise ValueError(
                    "Embedding vector dimension differs from the committed index"
                )
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('vector_dimension', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(dimension),),
            )

    def vector_dimension(self) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key='vector_dimension'"
            ).fetchone()
        return int(row["value"]) if row else None

    def status_payload(self, source_ids: set[str] | None = None) -> dict:
        sources = self.list_sources(source_ids)
        enabled_sources = [source for source in sources if source["enabled"]]
        blocking = [
            source
            for source in enabled_sources
            if source["status"] != SourceStatus.FRESH.value
        ]
        counts: dict[str, int] = {}
        for source in sources:
            counts[source["status"]] = counts.get(source["status"], 0) + 1
        return {
            "ready": bool(enabled_sources) and not blocking,
            "enabled_source_count": len(enabled_sources),
            "index_version": self.index_version(),
            "embedding_fingerprint": self.embedding_fingerprint(),
            "vector_dimension": self.vector_dimension(),
            "counts": counts,
            "blocking_sources": [
                {
                    "source_id": source["source_id"],
                    "relative_path": source["relative_path"],
                    "status": source["status"],
                    "error_code": source["error_code"],
                }
                for source in blocking
            ],
            "sources": sources,
        }

    def export_debug_json(self) -> str:
        return json.dumps(self.status_payload(), ensure_ascii=False, indent=2)
