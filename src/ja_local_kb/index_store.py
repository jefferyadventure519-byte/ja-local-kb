"""Rebuildable LanceDB chunk index."""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from pathlib import Path

import lancedb
import numpy as np
from lancedb.index import FTS

from .errors import IndexIntegrityError
from .models import ParsedChunk

TABLE_NAME = "knowledge_chunks"
FILTER_VALUES_RE = re.compile(r"'((?:''|[^'])*)'")


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LanceIndex:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(root)
        self._table_handle = (
            self.db.open_table(TABLE_NAME)
            if TABLE_NAME in self._table_names()
            else None
        )
        self._snapshot_lock = threading.Lock()
        self._snapshot_rows: list[dict] | None = None
        self._snapshot_matrix: np.ndarray | None = None
        self._snapshot_norms: np.ndarray | None = None

    def _table_names(self) -> list[str]:
        result = self.db.list_tables()
        return list(getattr(result, "tables", result))

    def exists(self) -> bool:
        return TABLE_NAME in self._table_names()

    def _table(self):
        if self._table_handle is not None:
            return self._table_handle
        if not self.exists():
            raise IndexIntegrityError("LanceDB chunk table is missing")
        self._table_handle = self.db.open_table(TABLE_NAME)
        return self._table_handle

    def rows_for_source(self, source_id: str) -> dict[str, dict]:
        if not self.exists():
            return {}
        rows = (
            self._table()
            .search()
            .where(f"source_id = {sql_literal(source_id)}")
            .limit(10000)
            .to_list()
        )
        return {row["chunk_id"]: row for row in rows}

    @staticmethod
    def make_row(chunk: ParsedChunk, vector: list[float]) -> dict:
        return {
            **chunk.model_dump(mode="json"),
            "vector": vector,
        }

    def replace_source(
        self,
        source_id: str,
        rows: Sequence[dict],
    ) -> None:
        if not rows:
            raise IndexIntegrityError(
                "A source must produce at least one searchable chunk",
                details={"source_id": source_id},
            )
        if not self.exists():
            self._table_handle = self.db.create_table(TABLE_NAME, data=list(rows))
        else:
            table = self._table()
            table.merge_insert(
                "chunk_id"
            ).when_matched_update_all().when_not_matched_insert_all().execute(
                list(rows)
            )
            keep_ids = {row["chunk_id"] for row in rows}
            old_ids = set(self.rows_for_source(source_id))
            obsolete = old_ids - keep_ids
            if obsolete:
                values = ", ".join(sql_literal(value) for value in sorted(obsolete))
                table.delete(
                    f"source_id = {sql_literal(source_id)} AND chunk_id IN ({values})"
                )
        self.rebuild_fts()
        self.validate_source(source_id, rows)
        self._invalidate_snapshot()

    def delete_source(self, source_id: str) -> int:
        rows = self.rows_for_source(source_id)
        if not rows:
            return 0
        table = self._table()
        table.delete(f"source_id = {sql_literal(source_id)}")
        if table.count_rows() > 0:
            self.rebuild_fts()
        self._invalidate_snapshot()
        return len(rows)

    def _invalidate_snapshot(self) -> None:
        with self._snapshot_lock:
            self._snapshot_rows = None
            self._snapshot_matrix = None
            self._snapshot_norms = None

    def _vector_snapshot(self) -> tuple[list[dict], np.ndarray, np.ndarray]:
        if (
            self._snapshot_rows is not None
            and self._snapshot_matrix is not None
            and self._snapshot_norms is not None
        ):
            return (
                self._snapshot_rows,
                self._snapshot_matrix,
                self._snapshot_norms,
            )
        with self._snapshot_lock:
            if (
                self._snapshot_rows is None
                or self._snapshot_matrix is None
                or self._snapshot_norms is None
            ):
                rows = self._table().search().limit(10000).to_list()
                matrix = np.asarray(
                    [row["vector"] for row in rows],
                    dtype=np.float32,
                )
                self._snapshot_rows = rows
                self._snapshot_matrix = matrix
                self._snapshot_norms = np.einsum(
                    "ij,ij->i",
                    matrix,
                    matrix,
                )
        return (
            self._snapshot_rows,
            self._snapshot_matrix,
            self._snapshot_norms,
        )

    @staticmethod
    def _where_values(where: str | None, field: str) -> set[str] | None:
        if not where:
            return None
        match = re.search(rf"\b{re.escape(field)}\s+IN\s+\(([^)]*)\)", where)
        if match is None:
            return None
        return {
            value.replace("''", "'")
            for value in FILTER_VALUES_RE.findall(match.group(1))
        }

    def rebuild_fts(self) -> None:
        self._table().create_index(
            "retrieval_text",
            config=FTS(base_tokenizer="icu"),
            replace=True,
        )

    def validate_source(self, source_id: str, expected_rows: Sequence[dict]) -> None:
        actual = self.rows_for_source(source_id)
        expected = {row["chunk_id"]: row for row in expected_rows}
        if set(actual) != set(expected):
            raise IndexIntegrityError(
                "Committed LanceDB chunk IDs do not match the parsed source",
                details={
                    "source_id": source_id,
                    "expected_count": len(expected),
                    "actual_count": len(actual),
                },
            )
        for chunk_id, expected_row in expected.items():
            actual_row = actual[chunk_id]
            if actual_row["embedding_hash"] != expected_row["embedding_hash"] or len(
                actual_row["vector"]
            ) != len(expected_row["vector"]):
                raise IndexIntegrityError(
                    "Committed LanceDB row failed vector-space validation",
                    details={"source_id": source_id, "chunk_id": chunk_id},
                )

    def vector_search(
        self,
        vector: list[float],
        *,
        limit: int,
        where: str | None = None,
    ) -> list[dict]:
        return self.vector_search_many(
            [vector],
            limit=limit,
            where=where,
        )[0]

    def vector_search_many(
        self,
        vectors: Sequence[list[float]],
        *,
        limit: int,
        where: str | None = None,
    ) -> list[list[dict]]:
        if not vectors:
            return []
        rows, matrix, row_norms = self._vector_snapshot()
        project_ids = self._where_values(where, "project_id")
        source_ids = self._where_values(where, "source_id")
        eligible = np.asarray(
            [
                (project_ids is None or row["project_id"] in project_ids)
                and (source_ids is None or row["source_id"] in source_ids)
                for row in rows
            ],
            dtype=bool,
        )
        query_matrix = np.asarray(vectors, dtype=np.float32)
        distances = (
            row_norms[:, np.newaxis]
            + np.einsum("ij,ij->i", query_matrix, query_matrix)[np.newaxis, :]
            - 2.0 * (matrix @ query_matrix.T)
        )
        distances[~eligible, :] = np.inf
        results = []
        for query_index in range(query_matrix.shape[0]):
            query_distances = distances[:, query_index]
            ordered = np.argsort(query_distances, kind="stable")[:limit]
            results.append(
                [
                    {
                        **rows[int(index)],
                        "_distance": float(query_distances[int(index)]),
                    }
                    for index in ordered
                    if np.isfinite(query_distances[int(index)])
                ]
            )
        return results

    def keyword_search(
        self,
        text: str,
        *,
        limit: int,
        where: str | None = None,
    ) -> list[dict]:
        query = self._table().search(text, query_type="fts")
        if where:
            query = query.where(where, prefilter=True)
        return query.limit(limit).to_list()

    def get_chunk(self, chunk_id: str) -> dict | None:
        rows = (
            self._table()
            .search()
            .where(f"chunk_id = {sql_literal(chunk_id)}")
            .limit(2)
            .to_list()
        )
        if len(rows) > 1:
            raise IndexIntegrityError(
                "Duplicate chunk identity in LanceDB",
                details={"chunk_id": chunk_id},
            )
        return rows[0] if rows else None
