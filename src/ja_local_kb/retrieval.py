"""Evidence-only keyword, vector, and reciprocal-rank hybrid retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .embedding import Embedder
from .index_store import LanceIndex, sql_literal
from .models import RetrievalConfig, SearchEvidence
from .multi_route import decompose_query, retrieve_multi_route
from .reranker import Reranker
from .state import StateStore

SearchMode = Literal[
    "keyword",
    "vector",
    "hybrid",
    "smart",
    "recall",
    "quality",
]
COMPLEX_CUES = (
    "多个项目",
    "跨项目",
    "分别",
    "共同",
    "对比",
    "冲突",
    "演变",
    "证据链",
    "综合",
    "为什么",
    "哪些",
    "哪个",
    "同时",
    "边界",
    "保持分离",
)


@dataclass(frozen=True)
class SearchPlan:
    requested_mode: SearchMode
    executed_mode: Literal["keyword", "vector", "hybrid"]
    candidate_k: int
    top_k: int
    complex_query: bool


def build_filter(
    project_ids: Sequence[str] | None,
    source_ids: Sequence[str],
) -> str:
    source_values = ", ".join(sql_literal(value) for value in sorted(set(source_ids)))
    clauses = [f"source_id IN ({source_values})"]
    if project_ids:
        project_values = ", ".join(
            sql_literal(value) for value in sorted(set(project_ids))
        )
        clauses.append(f"project_id IN ({project_values})")
    return " AND ".join(clauses)


def plan_search(
    query: str,
    config: RetrievalConfig,
    *,
    mode: SearchMode,
    top_k: int | None,
) -> SearchPlan:
    if mode not in {"keyword", "vector", "hybrid", "smart", "recall", "quality"}:
        raise ValueError(f"Unsupported search mode: {mode}")
    if top_k is not None and top_k < 1:
        raise ValueError("top_k must be at least 1")
    subqueries = decompose_query(query, max_subqueries=config.smart_max_subqueries)
    complex_query = (
        len(query) >= 80
        or len(subqueries) > 1
        or any(cue in query for cue in COMPLEX_CUES)
    )
    default_top_k = (
        config.recall_candidate_k
        if mode == "recall"
        else config.quality_top_k
        if mode == "quality"
        else config.complex_top_k
        if mode == "smart" and complex_query
        else config.evidence_max
        if complex_query
        else config.simple_top_k
    )
    effective_top_k = top_k or default_top_k
    effective_top_k = max(
        1,
        min(
            effective_top_k,
            config.recall_candidate_k if mode == "recall" else config.evidence_max,
        ),
    )
    executed = "hybrid" if mode in {"smart", "recall", "quality"} else mode
    candidate_k = (
        config.recall_candidate_k
        if mode == "recall"
        else max(effective_top_k, config.quality_candidate_k)
        if mode == "quality"
        else max(effective_top_k, config.smart_candidate_k)
        if mode == "smart" and complex_query
        else max(
            effective_top_k,
            config.complex_candidate_k if complex_query else effective_top_k * 2,
        )
    )
    return SearchPlan(
        requested_mode=mode,
        executed_mode=executed,
        candidate_k=candidate_k,
        top_k=effective_top_k,
        complex_query=complex_query,
    )


class Retriever:
    def __init__(
        self,
        index: LanceIndex,
        state: StateStore,
        embedder: Embedder,
        config: RetrievalConfig,
        reranker: Reranker | None = None,
    ) -> None:
        self.index = index
        self.state = state
        self.embedder = embedder
        self.config = config
        self.reranker = reranker

    def search(
        self,
        query: str,
        *,
        mode: SearchMode = "recall",
        top_k: int | None = None,
        project_ids: Sequence[str] | None = None,
        source_ids: Sequence[str],
        include_candidates: bool = False,
    ) -> dict:
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        if len(normalized) > 20000:
            raise ValueError("query is too long")
        if include_candidates and mode != "quality":
            raise ValueError("include_candidates is only available in quality mode")
        plan = plan_search(
            normalized,
            self.config,
            mode=mode,
            top_k=top_k,
        )
        if not source_ids:
            raise ValueError("No enabled sources are available")
        if mode == "quality":
            return self._search_quality(
                normalized,
                plan=plan,
                project_ids=project_ids,
                source_ids=source_ids,
                include_candidates=include_candidates,
            )
        if mode == "recall":
            return self._search_recall(
                normalized,
                plan=plan,
                project_ids=project_ids,
                source_ids=source_ids,
            )
        if mode == "smart" and plan.complex_query:
            return self._search_multi_route(
                normalized,
                plan=plan,
                project_ids=project_ids,
                source_ids=source_ids,
            )
        where = build_filter(project_ids, source_ids)
        keyword_rows: list[dict] = []
        vector_rows: list[dict] = []
        if plan.executed_mode in {"keyword", "hybrid"}:
            keyword_rows = self.index.keyword_search(
                normalized,
                limit=plan.candidate_k,
                where=where,
            )
        if plan.executed_mode in {"vector", "hybrid"}:
            query_vectors = self.embedder.embed([normalized])
            if len(query_vectors) != 1:
                raise ValueError("Query embedding count mismatch")
            expected_dimension = self.state.vector_dimension()
            if (
                expected_dimension is None
                or len(query_vectors[0]) != expected_dimension
            ):
                raise ValueError("Query embedding vector space mismatch")
            vector_rows = self.index.vector_search(
                query_vectors[0],
                limit=plan.candidate_k,
                where=where,
            )
        fused = self._fuse(keyword_rows, vector_rows)
        version = self.state.index_version()
        evidence = [
            self._evidence(
                row,
                lexical_rank=lexical_rank,
                vector_rank=vector_rank,
                score=score,
                index_version=version,
            ).model_dump(mode="json")
            for row, lexical_rank, vector_rank, score in fused[: plan.top_k]
        ]
        return {
            "query": normalized,
            "requested_mode": plan.requested_mode,
            "executed_mode": plan.executed_mode,
            "complex_query": plan.complex_query,
            "project_ids": list(project_ids or []),
            "index_version": version,
            "freshness": "fresh",
            "evidence_count": len(evidence),
            "evidence": evidence,
        }

    def _search_quality(
        self,
        query: str,
        *,
        plan: SearchPlan,
        project_ids: Sequence[str] | None,
        source_ids: Sequence[str],
        include_candidates: bool,
    ) -> dict:
        if self.reranker is None:
            raise ValueError("quality mode requires a configured reranker")
        expected_dimension = self.state.vector_dimension()
        if expected_dimension is None:
            raise ValueError("Query embedding vector space mismatch")
        result = retrieve_multi_route(
            index=self.index,
            embedder=self.embedder,
            config=self.config,
            query=query,
            top_k=plan.top_k,
            candidate_k=plan.candidate_k,
            where=build_filter(project_ids, source_ids),
            expected_dimension=expected_dimension,
        )
        candidates = result.candidate_pool[: plan.candidate_k]
        if not candidates:
            return {
                "query": query,
                "requested_mode": plan.requested_mode,
                "executed_mode": plan.executed_mode,
                "retrieval_strategy": "high_recall_rerank_v1",
                "complex_query": plan.complex_query,
                "subqueries": result.subqueries,
                "vector_subqueries": result.vector_subqueries,
                "project_ids": list(project_ids or []),
                "routed_projects": result.routed_projects,
                "project_scores": result.project_scores,
                "candidate_count": 0,
                "reranker_model": self.reranker.fingerprint,
                "index_version": self.state.index_version(),
                "freshness": "fresh",
                "evidence_count": 0,
                "evidence": [],
            }
        rerank_query = self._rerank_query(query, result.subqueries)
        ranked = self.reranker.rerank(
            rerank_query,
            [self._rerank_document(candidate.row) for candidate in candidates],
            top_n=len(candidates),
        )
        version = self.state.index_version()
        candidate_ranks = {
            candidate.row["chunk_id"]: rank
            for rank, candidate in enumerate(candidates, start=1)
        }
        final_ranked = self._select_diverse_ranked(
            candidates,
            ranked,
            top_k=plan.top_k,
            routed_projects=result.routed_projects,
        )
        rerank_ranks = {item.index: rank for rank, item in enumerate(ranked, start=1)}
        evidence = []
        for item in final_ranked:
            candidate = candidates[item.index]
            evidence.append(
                self._evidence(
                    candidate.row,
                    lexical_rank=candidate.lexical_rank,
                    vector_rank=candidate.vector_rank,
                    score=candidate.score,
                    index_version=version,
                    candidate_rank=candidate_ranks[candidate.row["chunk_id"]],
                    rerank_rank=rerank_ranks[item.index],
                    rerank_score=item.score,
                ).model_dump(mode="json")
            )
        payload = {
            "query": query,
            "requested_mode": plan.requested_mode,
            "executed_mode": plan.executed_mode,
            "retrieval_strategy": "high_recall_rerank_v1",
            "complex_query": plan.complex_query,
            "subqueries": result.subqueries,
            "vector_subqueries": result.vector_subqueries,
            "rerank_query": rerank_query,
            "project_ids": list(project_ids or []),
            "routed_projects": result.routed_projects,
            "project_scores": result.project_scores,
            "candidate_count": len(candidates),
            "candidate_limit": plan.candidate_k,
            "reranker_model": self.reranker.fingerprint,
            "diversity_policy": "routed_project_floor_unique_heading_source_cap_v2",
            "index_version": version,
            "freshness": "fresh",
            "evidence_count": len(evidence),
            "evidence": evidence,
        }
        if include_candidates:
            payload["candidate_evidence"] = [
                self._evidence(
                    candidate.row,
                    lexical_rank=candidate.lexical_rank,
                    vector_rank=candidate.vector_rank,
                    score=candidate.score,
                    index_version=version,
                    candidate_rank=rank,
                ).model_dump(mode="json")
                for rank, candidate in enumerate(candidates, start=1)
            ]
        return payload

    def _search_recall(
        self,
        query: str,
        *,
        plan: SearchPlan,
        project_ids: Sequence[str] | None,
        source_ids: Sequence[str],
    ) -> dict:
        expected_dimension = self.state.vector_dimension()
        if expected_dimension is None:
            raise ValueError("Query embedding vector space mismatch")
        result = retrieve_multi_route(
            index=self.index,
            embedder=self.embedder,
            config=self.config,
            query=query,
            top_k=plan.top_k,
            candidate_k=plan.candidate_k,
            where=build_filter(project_ids, source_ids),
            expected_dimension=expected_dimension,
        )
        version = self.state.index_version()
        candidates = result.candidate_pool[: plan.top_k]
        evidence = [
            self._evidence(
                candidate.row,
                lexical_rank=candidate.lexical_rank,
                vector_rank=candidate.vector_rank,
                score=candidate.score,
                index_version=version,
                candidate_rank=rank,
            ).model_dump(mode="json")
            for rank, candidate in enumerate(candidates, start=1)
        ]
        return {
            "query": query,
            "requested_mode": plan.requested_mode,
            "executed_mode": plan.executed_mode,
            "retrieval_strategy": "high_recall_candidate_v1",
            "evidence_profile": "complete_candidate_pool",
            "complex_query": plan.complex_query,
            "subqueries": result.subqueries,
            "vector_subqueries": result.vector_subqueries,
            "project_ids": list(project_ids or []),
            "routed_projects": result.routed_projects,
            "project_scores": result.project_scores,
            "candidate_count": len(candidates),
            "candidate_limit": plan.candidate_k,
            "index_version": version,
            "freshness": "fresh",
            "evidence_count": len(evidence),
            "evidence": evidence,
        }

    @staticmethod
    def _rerank_query(query: str, subqueries: Sequence[str]) -> str:
        independent_needs = [subquery for subquery in subqueries if subquery != query]
        if not independent_needs:
            return query
        bullets = "\n".join(f"- {subquery}" for subquery in independent_needs)
        return (
            f"Original user question:\n{query}\n\n"
            "Independent evidence needs (a passage may support any one need):\n"
            f"{bullets}"
        )

    @staticmethod
    def _select_diverse_ranked(
        candidates: Sequence,
        ranked: Sequence,
        *,
        top_k: int,
        routed_projects: Sequence[str] = (),
    ) -> list:
        """Preserve reranker order while limiting redundant source sections."""
        selected: list = []
        selected_indexes: set[int] = set()
        selected_headings: set[tuple[str, str, str]] = set()
        source_counts: dict[str, int] = {}
        source_cap = max(2, math.ceil(top_k * 0.25))

        def add(item, *, enforce_source_cap: bool, enforce_heading: bool) -> None:
            if item.index in selected_indexes or len(selected) >= top_k:
                return
            row = candidates[item.index].row
            source_id = str(row.get("source_id", ""))
            heading_key = (
                str(row.get("project_id", "")),
                str(row.get("relative_path", "")),
                str(row.get("heading", "")),
            )
            if enforce_heading and heading_key in selected_headings:
                return
            if enforce_source_cap and source_counts.get(source_id, 0) >= source_cap:
                return
            selected.append(item)
            selected_indexes.add(item.index)
            selected_headings.add(heading_key)
            source_counts[source_id] = source_counts.get(source_id, 0) + 1

        for project_id in routed_projects:
            best_project_item = next(
                (
                    item
                    for item in ranked
                    if str(candidates[item.index].row.get("project_id", ""))
                    == project_id
                ),
                None,
            )
            if best_project_item is not None:
                add(
                    best_project_item,
                    enforce_source_cap=False,
                    enforce_heading=True,
                )
        for item in ranked:
            add(item, enforce_source_cap=True, enforce_heading=True)
        for item in ranked:
            add(item, enforce_source_cap=False, enforce_heading=True)
        for item in ranked:
            add(item, enforce_source_cap=False, enforce_heading=False)
        rerank_order = {item.index: rank for rank, item in enumerate(ranked, start=1)}
        return sorted(selected, key=lambda item: rerank_order[item.index])

    def _search_multi_route(
        self,
        query: str,
        *,
        plan: SearchPlan,
        project_ids: Sequence[str] | None,
        source_ids: Sequence[str],
    ) -> dict:
        expected_dimension = self.state.vector_dimension()
        if expected_dimension is None:
            raise ValueError("Query embedding vector space mismatch")
        result = retrieve_multi_route(
            index=self.index,
            embedder=self.embedder,
            config=self.config,
            query=query,
            top_k=plan.top_k,
            candidate_k=plan.candidate_k,
            where=build_filter(project_ids, source_ids),
            expected_dimension=expected_dimension,
        )
        version = self.state.index_version()
        evidence = [
            self._evidence(
                candidate.row,
                lexical_rank=candidate.lexical_rank,
                vector_rank=candidate.vector_rank,
                score=candidate.score,
                index_version=version,
            ).model_dump(mode="json")
            for candidate in result.candidates
        ]
        return {
            "query": query,
            "requested_mode": plan.requested_mode,
            "executed_mode": plan.executed_mode,
            "retrieval_strategy": "deterministic_multi_route_v5",
            "complex_query": True,
            "subqueries": result.subqueries,
            "vector_subqueries": result.vector_subqueries,
            "project_ids": list(project_ids or []),
            "routed_projects": result.routed_projects,
            "project_scores": result.project_scores,
            "index_version": version,
            "freshness": "fresh",
            "evidence_count": len(evidence),
            "evidence": evidence,
        }

    @staticmethod
    def _fuse(
        keyword_rows: list[dict],
        vector_rows: list[dict],
    ) -> list[tuple[dict, int | None, int | None, float]]:
        rows: dict[str, dict] = {}
        lexical_ranks: dict[str, int] = {}
        vector_ranks: dict[str, int] = {}
        scores: dict[str, float] = {}
        for rank, row in enumerate(keyword_rows, start=1):
            chunk_id = row["chunk_id"]
            rows[chunk_id] = row
            lexical_ranks[chunk_id] = rank
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (60 + rank)
        for rank, row in enumerate(vector_rows, start=1):
            chunk_id = row["chunk_id"]
            rows[chunk_id] = row
            vector_ranks[chunk_id] = rank
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (60 + rank)
        ordered = sorted(
            rows,
            key=lambda chunk_id: (
                -scores[chunk_id],
                lexical_ranks.get(chunk_id, 10**9),
                vector_ranks.get(chunk_id, 10**9),
                chunk_id,
            ),
        )
        return [
            (
                rows[chunk_id],
                lexical_ranks.get(chunk_id),
                vector_ranks.get(chunk_id),
                scores[chunk_id],
            )
            for chunk_id in ordered
        ]

    @staticmethod
    def _evidence(
        row: dict,
        *,
        lexical_rank: int | None,
        vector_rank: int | None,
        score: float,
        index_version: int,
        candidate_rank: int | None = None,
        rerank_rank: int | None = None,
        rerank_score: float | None = None,
    ) -> SearchEvidence:
        return SearchEvidence(
            evidence_id=f"ev_{row['chunk_id']}",
            chunk_id=row["chunk_id"],
            source_id=row["source_id"],
            project_id=row["project_id"],
            project_name=row["project_name"],
            client_id=row["client_id"],
            document_role=row["document_role"],
            relative_path=row["relative_path"],
            heading=row["heading"],
            source_text=row["source_text"],
            document_status=row["document_status"],
            updated=row["updated"],
            lexical_rank=lexical_rank,
            vector_rank=vector_rank,
            candidate_rank=candidate_rank,
            rerank_rank=rerank_rank,
            rerank_score=rerank_score,
            fused_score=score,
            index_version=index_version,
            freshness="fresh",
        )

    @staticmethod
    def _rerank_document(row: dict) -> str:
        return "\n".join(
            [
                f"Project: {row.get('project_name', '')}",
                f"Document role: {row.get('document_role', '')}",
                f"Document status: {row.get('document_status', '')}",
                f"Updated: {row.get('updated', '')}",
                f"Heading: {row.get('heading', '')}",
                "Content:",
                str(row.get("source_text", "")),
            ]
        )
