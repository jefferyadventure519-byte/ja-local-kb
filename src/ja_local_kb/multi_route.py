"""Deterministic multi-query retrieval for complex, cross-project questions."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .embedding import Embedder
from .index_store import LanceIndex
from .models import RetrievalConfig

CLAUSE_SPLIT_RE = re.compile(r"[，,；;。！？?!\n]+")
HEADING_PART_RE = re.compile(r"\s*\[\d+\]\s*$")
ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
ENUMERATION_SPLIT_RE = re.compile(r"[、]|和")
ENUMERATION_INTENT_RE = re.compile(r"^.*?(?:管理|比较|对比|涉及|包括|覆盖|关联|连接)")
WRITE_WORKFLOW_RE = re.compile(r"(?:自动|静默|直接)?(?:写入|直写|写进|提交)")
WRITE_GUARD_QUERY = "写入流程；preview；人工确认；权限；审批；回滚；安全边界"
KNOWLEDGE_DOMAINS = ("客户知识", "个人项目", "会议资料", "政府扫标")
GOVERNANCE_BOUNDARY_QUERY = (
    "多个知识域是否统一治理；知识平台和 JA-Wiki 的当前职责范围；独立体系与边界"
)
CROSS_DEVICE_TRACE_QUERY = (
    "跨设备来源追溯；本地知识的最小元数据与双链；"
    "完整路径和同名页面的关系必须精确解析；关系不等于文件同步"
)
DOCUMENT_ROLE_PRIOR = {
    "decision_log": 0.070,
    "rule_library": 0.055,
    "background_requirements": 0.045,
    "requirement_context": 0.045,
    "project_overview": 0.025,
    "sedimentation_sop": 0.015,
    "sop": 0.015,
    "project_timeline": 0.000,
    "timeline": 0.000,
    "asset_index": -0.025,
}


@dataclass(frozen=True)
class SelectedCandidate:
    row: dict
    lexical_rank: int | None
    vector_rank: int | None
    score: float


@dataclass(frozen=True)
class MultiRouteResult:
    subqueries: list[str]
    vector_subqueries: list[str]
    routed_projects: list[str]
    project_scores: list[dict]
    candidates: list[SelectedCandidate]
    candidate_pool: list[SelectedCandidate]


def decompose_query(query: str, max_subqueries: int = 4) -> list[str]:
    """Create a small query set without expected answers or benchmark labels."""
    normalized = re.sub(r"\s+", " ", query).strip()
    clauses = [
        clause.strip(" ：:")
        for clause in CLAUSE_SPLIT_RE.split(normalized)
        if len(clause.strip(" ：:")) >= 5
    ]
    subqueries = [normalized]
    if WRITE_WORKFLOW_RE.search(normalized):
        subqueries.append(WRITE_GUARD_QUERY)
    mentioned_domains = sum(domain in normalized for domain in KNOWLEDGE_DOMAINS)
    if (
        mentioned_domains >= 3
        and "统一" in normalized
        and any(cue in normalized for cue in ("规则", "管理", "治理"))
    ):
        subqueries.append(GOVERNANCE_BOUNDARY_QUERY)
    if (
        any(cue in normalized for cue in ("其他电脑", "别的电脑", "跨设备"))
        and any(cue in normalized for cue in ("本地文件", "本地路径"))
        and any(cue in normalized for cue in ("来源", "追溯", "同步"))
    ):
        subqueries.append(CROSS_DEVICE_TRACE_QUERY)
    for clause in clauses:
        if "、" not in clause:
            continue
        parts = [
            part.strip()
            for part in ENUMERATION_SPLIT_RE.split(clause)
            if len(part.strip()) >= 2
        ]
        if len(parts) < 3:
            continue
        first_label = ENUMERATION_INTENT_RE.sub("", parts[0]).strip()
        if not first_label:
            continue
        intent = parts[0][: -len(first_label)].strip()
        constraint = clauses[-1] if clauses[-1] != clause else ""
        for index, part in enumerate(parts):
            label = first_label if index == 0 else part
            focused = f"{intent}{label}"
            if constraint:
                focused = f"{focused}；{constraint}"
            if focused not in subqueries:
                subqueries.append(focused)
            if len(subqueries) >= max_subqueries:
                return subqueries
        break
    anchor = clauses[0] if clauses else ""
    for clause_index, clause in enumerate(clauses):
        if clause == normalized:
            continue
        anchored = clause if clause_index == 0 or not anchor else f"{anchor}；{clause}"
        if anchored in subqueries:
            continue
        subqueries.append(anchored)
        if len(subqueries) >= max_subqueries:
            break
    return subqueries


def format_embedding_query(query: str) -> str:
    return (
        "Instruct: Retrieve passages that provide the facts, current decisions, "
        "constraints, and source evidence needed to answer the query. Prefer "
        "current decisions over superseded ones.\n"
        f"Query: {query}"
    )


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _char_ngrams(value: str, size: int = 2) -> set[str]:
    compact = _compact(value)
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _heading_relevance(query: str, row: dict) -> float:
    heading_grams = _char_ngrams(str(row.get("heading", "")))
    if not heading_grams:
        return 0.0
    query_grams = _char_ngrams(query)
    coverage = len(query_grams & heading_grams) / len(heading_grams)
    return min(0.085, coverage * 0.11)


def _project_mention_strength(query: str, project_name: str) -> float:
    query_lower = query.lower()
    project_lower = project_name.lower()
    score = 0.0
    for token in ASCII_TOKEN_RE.findall(project_lower):
        if len(token) >= 2 and token in query_lower:
            score = max(score, min(0.13, 0.025 * len(token)))
    query_compact = _compact(query)
    for part in CHINESE_RE.findall(project_name):
        for size in range(min(6, len(part)), 1, -1):
            if any(
                part[index : index + size] in query_compact
                for index in range(len(part) - size + 1)
            ):
                score = max(score, min(0.13, 0.025 * size))
                break
    if (
        any(phrase in query for phrase in ("本地项目", "本地文件", "项目执行状态"))
        and "个人项目" in project_name
    ):
        score = max(score, 0.13)
    return score


def _heading_key(row: dict) -> str:
    heading = HEADING_PART_RE.sub("", str(row.get("heading", ""))).strip()
    return "|".join(
        [
            str(row.get("project_id", "")),
            str(row.get("relative_path", "")),
            heading,
        ]
    )


def _content_signature(row: dict) -> set[str]:
    text = re.sub(r"\s+", "", str(row.get("source_text", "")))
    if len(text) < 5:
        return {text} if text else set()
    return {text[index : index + 5] for index in range(len(text) - 4)}


def _too_similar(
    row: dict,
    selected: list[dict],
    threshold: float = 0.78,
) -> bool:
    signature = _content_signature(row)
    if not signature:
        return False
    for existing in selected:
        other = _content_signature(existing)
        union = signature | other
        if union and len(signature & other) / len(union) >= threshold:
            return True
    return False


def _normalize_vector(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(math.fsum(float(value) ** 2 for value in vector))
    if norm <= 1e-12:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def _route_count(
    project_scores: list[tuple[str, float]],
    subquery_count: int,
) -> int:
    if len(project_scores) <= 3:
        return len(project_scores)
    first = project_scores[0][1]
    count = 3
    thresholds = {4: 0.50, 5: 0.45, 6: 0.40}
    for candidate_count in range(4, min(6, len(project_scores)) + 1):
        if project_scores[candidate_count - 1][1] >= (
            first * thresholds[candidate_count]
        ):
            count = candidate_count
    diversity_floor = min(len(project_scores), min(5, subquery_count + 2))
    return max(count, diversity_floor)


def _quotas(project_ids: list[str], top_k: int) -> dict[str, int]:
    if not project_ids:
        return {}
    if len(project_ids) == 1:
        return {project_ids[0]: top_k}

    # Complex questions normally have one primary project and several supporting
    # projects. Equal quotas truncate the primary evidence trail while returning
    # too many marginal passages from low-confidence routes. Keep one evidence
    # slot per route, then distribute the remaining capacity by route rank.
    rank_weights = (0.72, 0.20, 0.05, 0.02, 0.01)
    weights = [
        rank_weights[index] if index < len(rank_weights) else rank_weights[-1]
        for index in range(len(project_ids))
    ]
    weight_total = sum(weights)
    quotas = [1 for _ in project_ids]
    remaining = max(0, top_k - len(project_ids))
    exact = [remaining * weight / weight_total for weight in weights]
    additions = [int(value) for value in exact]
    for index, addition in enumerate(additions):
        quotas[index] += addition
    remainder = remaining - sum(additions)
    fractional_order = sorted(
        range(len(project_ids)),
        key=lambda index: (exact[index] - additions[index], -index),
        reverse=True,
    )
    for index in fractional_order[:remainder]:
        quotas[index] += 1
    return dict(zip(project_ids, quotas, strict=True))


def _item_score(item: dict) -> float:
    return (
        float(item["route_score"])
        + float(item["quality_bonus"])
        + float(item.get("semantic_relevance", 0.0))
    )


def _fuse_rows(
    keyword_rows: list[dict],
    vector_rows: list[dict],
) -> list[tuple[dict, int | None, int | None, float]]:
    rows: dict[str, dict] = {}
    lexical_ranks: dict[str, int] = {}
    vector_ranks: dict[str, int] = {}
    scores: dict[str, float] = {}
    for rank, row in enumerate(keyword_rows, start=1):
        chunk_id = str(row["chunk_id"])
        rows[chunk_id] = row
        lexical_ranks[chunk_id] = rank
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
    for rank, row in enumerate(vector_rows, start=1):
        chunk_id = str(row["chunk_id"])
        rows[chunk_id] = row
        vector_ranks[chunk_id] = rank
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.15 / (60 + rank)
    ordered = sorted(
        rows,
        key=lambda chunk_id: (
            -scores[chunk_id],
            vector_ranks.get(chunk_id, 10**9),
            lexical_ranks.get(chunk_id, 10**9),
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


def retrieve_multi_route(
    *,
    index: LanceIndex,
    embedder: Embedder,
    config: RetrievalConfig,
    query: str,
    top_k: int,
    candidate_k: int,
    where: str,
    expected_dimension: int,
) -> MultiRouteResult:
    subqueries = decompose_query(
        query,
        max_subqueries=config.smart_max_subqueries,
    )
    vector_subqueries = subqueries[: config.smart_vector_subqueries]
    query_vectors = embedder.embed(
        [format_embedding_query(item) for item in vector_subqueries]
    )
    if len(query_vectors) != len(vector_subqueries):
        raise ValueError("Query embedding count mismatch")
    if any(len(vector) != expected_dimension for vector in query_vectors):
        raise ValueError("Query embedding vector space mismatch")
    normalized_queries = [_normalize_vector(vector) for vector in query_vectors]
    candidates: dict[str, dict] = {}
    project_query_best: dict[str, dict[int, int]] = defaultdict(dict)
    project_names: dict[str, str] = {}

    task_count = len(subqueries) + 1
    with ThreadPoolExecutor(max_workers=min(8, task_count)) as executor:
        keyword_futures = [
            executor.submit(
                index.keyword_search,
                subquery,
                limit=candidate_k,
                where=where,
            )
            for subquery in subqueries
        ]
        vector_future = executor.submit(
            index.vector_search_many,
            query_vectors,
            limit=candidate_k,
            where=where,
        )
        keyword_results = [future.result() for future in keyword_futures]
        vector_results = vector_future.result()

    for query_index, _subquery in enumerate(subqueries):
        keyword_rows = keyword_results[query_index]
        vector_rows = (
            vector_results[query_index] if query_index < len(vector_results) else []
        )
        fused = _fuse_rows(keyword_rows, vector_rows)
        query_weight = 1.35 if query_index == 0 else 1.0
        for rank, (row, lexical_rank, vector_rank, _) in enumerate(
            fused[:candidate_k],
            start=1,
        ):
            chunk_id = str(row["chunk_id"])
            project_id = str(row["project_id"])
            project_names[project_id] = str(row.get("project_name", ""))
            item = candidates.setdefault(
                chunk_id,
                {
                    "row": row,
                    "route_score": 0.0,
                    "query_ranks": {},
                    "lexical_rank": None,
                    "vector_rank": None,
                    "quality_bonus": (
                        DOCUMENT_ROLE_PRIOR.get(
                            str(row.get("document_role", "")),
                            0.0,
                        )
                        + _heading_relevance(query, row)
                    ),
                },
            )
            item["route_score"] += query_weight / (12.0 + rank)
            item["query_ranks"][query_index] = rank
            if lexical_rank is not None and (
                item["lexical_rank"] is None or lexical_rank < item["lexical_rank"]
            ):
                item["lexical_rank"] = lexical_rank
            if vector_rank is not None and (
                item["vector_rank"] is None or vector_rank < item["vector_rank"]
            ):
                item["vector_rank"] = vector_rank
            current_best = project_query_best[project_id].get(query_index)
            if current_best is None or rank < current_best:
                project_query_best[project_id][query_index] = rank

    for item in candidates.values():
        raw_vector = item["row"].get("vector")
        if raw_vector is None:
            item["assigned_subquery"] = min(
                item["query_ranks"],
                key=item["query_ranks"].get,
            )
            item["semantic_relevance"] = 0.0
            continue
        candidate_vector = _normalize_vector(list(raw_vector))
        scores = [
            _cosine(candidate_vector, query_vector)
            for query_vector in normalized_queries
        ]
        specific_scores = scores[1:] if len(scores) > 1 else scores
        item["assigned_subquery"] = (
            1 + max(range(len(specific_scores)), key=specific_scores.__getitem__)
            if len(scores) > 1
            else min(item["query_ranks"], key=item["query_ranks"].get)
        )
        item["semantic_relevance"] = 0.34 * max(scores) + 0.16 * (
            sum(scores) / len(scores)
        )

    project_scores: list[tuple[str, float]] = []
    for project_id, rank_map in project_query_best.items():
        score = 0.0
        for query_index, rank in rank_map.items():
            query_weight = 1.35 if query_index == 0 else 1.0
            score += query_weight / (8.0 + rank)
        score += _project_mention_strength(
            query,
            project_names.get(project_id, ""),
        )
        project_scores.append((project_id, score))
    project_scores.sort(key=lambda item: item[1], reverse=True)
    routed_count = _route_count(project_scores, len(subqueries))
    routed_projects = [project_id for project_id, _ in project_scores[:routed_count]]
    quotas = _quotas(routed_projects, top_k)

    by_project: dict[str, list[dict]] = defaultdict(list)
    for item in candidates.values():
        project_id = str(item["row"]["project_id"])
        if project_id in quotas:
            by_project[project_id].append(item)
    for items in by_project.values():
        items.sort(
            key=lambda item: (
                _item_score(item),
                len(item["query_ranks"]),
                -min(item["query_ranks"].values()),
            ),
            reverse=True,
        )

    selected: list[dict] = []
    selected_rows: list[dict] = []
    selected_ids: set[str] = set()
    selected_headings: set[str] = set()
    for project_id in routed_projects:
        project_selected: list[dict] = []
        project_items = list(by_project[project_id])
        covered_assignments: set[int] = set()
        while project_items and len(project_selected) < quotas[project_id]:
            item = max(
                project_items,
                key=lambda candidate: (
                    _item_score(candidate)
                    + (
                        0.085
                        if candidate.get("assigned_subquery") not in covered_assignments
                        else 0.0
                    )
                ),
            )
            project_items.remove(item)
            row = item["row"]
            chunk_id = str(row["chunk_id"])
            heading_key = _heading_key(row)
            if chunk_id in selected_ids or heading_key in selected_headings:
                continue
            if _too_similar(row, project_selected):
                continue
            selected.append(item)
            selected_rows.append(row)
            project_selected.append(row)
            selected_ids.add(chunk_id)
            selected_headings.add(heading_key)
            covered_assignments.add(int(item.get("assigned_subquery", 0)))

    if len(selected) < top_k:
        for item in sorted(candidates.values(), key=_item_score, reverse=True):
            row = item["row"]
            chunk_id = str(row["chunk_id"])
            heading_key = _heading_key(row)
            if chunk_id in selected_ids or heading_key in selected_headings:
                continue
            if _too_similar(row, selected_rows):
                continue
            selected.append(item)
            selected_rows.append(row)
            selected_ids.add(chunk_id)
            selected_headings.add(heading_key)
            if len(selected) >= top_k:
                break

    project_score_payload = [
        {
            "project_id": project_id,
            "score": round(score, 8),
            "routed": project_id in routed_projects,
            "quota": quotas.get(project_id, 0),
        }
        for project_id, score in project_scores
    ]
    return MultiRouteResult(
        subqueries=subqueries,
        vector_subqueries=vector_subqueries,
        routed_projects=routed_projects,
        project_scores=project_score_payload,
        candidates=[
            SelectedCandidate(
                row=item["row"],
                lexical_rank=item["lexical_rank"],
                vector_rank=item["vector_rank"],
                score=_item_score(item),
            )
            for item in selected[:top_k]
        ],
        candidate_pool=[
            SelectedCandidate(
                row=item["row"],
                lexical_rank=item["lexical_rank"],
                vector_rank=item["vector_rank"],
                score=_item_score(item),
            )
            for item in sorted(candidates.values(), key=_item_score, reverse=True)
        ],
    )
