"""Locked-question retrieval benchmark for external, non-repository datasets."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AppSettings
from .parser import parse_source
from .quality_report import attach_corpus_snapshot
from .registry import load_registry, resolve_source_path
from .service import KnowledgeService


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def group_rank(rows: list[dict[str, Any]], group: dict[str, Any]) -> int | None:
    for index, row in enumerate(rows, start=1):
        if row.get("project_id") != group["project_id"]:
            continue
        searchable = f"{row.get('heading', '')}\n{row.get('source_text', '')}"
        if all(marker in searchable for marker in group["markers"]):
            return index
    return None


def load_questions(
    path: Path,
    corrections_path: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if corrections_path is not None:
        corrections_raw = corrections_path.read_bytes()
        corrections = json.loads(corrections_raw.decode("utf-8"))
        source_sha256 = hashlib.sha256(raw).hexdigest()
        if corrections.get("source_questions_sha256") != source_sha256:
            raise ValueError(
                "Benchmark correction source hash does not match the locked questions"
            )
        groups = {
            (question["id"], group["id"]): group
            for question in payload.get("questions", [])
            for group in question.get("evidence_groups", [])
        }
        for correction in corrections.get("corrections", []):
            key = (correction["question_id"], correction["group_id"])
            if key not in groups:
                raise ValueError(
                    "Benchmark correction targets an unknown evidence group: "
                    f"{key[0]}/{key[1]}"
                )
            group = groups[key]
            if group.get("markers") != correction.get("previous_markers"):
                raise ValueError(
                    "Benchmark correction previous markers do not match: "
                    f"{key[0]}/{key[1]}"
                )
            group["markers"] = correction["current_markers"]
        payload["_evaluation_corrections"] = {
            "source_questions_sha256": source_sha256,
            "corrections_sha256": hashlib.sha256(corrections_raw).hexdigest(),
            "corrections": corrections.get("corrections", []),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("Benchmark questions must be a non-empty list")
    for question in questions:
        if not question.get("id") or not question.get("query"):
            raise ValueError("Every benchmark question requires id and query")
        if not question.get("evidence_groups"):
            raise ValueError(
                f"Question {question.get('id', '<unknown>')} has no evidence groups"
            )
    return raw, payload


def validate_corpus(
    settings: AppSettings,
    question_payload: dict[str, Any],
) -> dict[str, Any]:
    registry = load_registry(settings.source_registry)
    rows: list[dict[str, Any]] = []
    for source in registry.sources:
        if not source.enabled:
            continue
        path = resolve_source_path(settings.vault_root, source)
        rows.extend(
            {
                "project_id": chunk.project_id,
                "heading": chunk.heading,
                "source_text": chunk.source_text,
            }
            for chunk in parse_source(path, source, settings.chunking)
        )
    missing = []
    for question in question_payload["questions"]:
        for group in question["evidence_groups"]:
            if group_rank(rows, group) is None:
                missing.append(
                    {
                        "question_id": question["id"],
                        "group_id": group["id"],
                        "project_id": group["project_id"],
                        "markers": group["markers"],
                    }
                )
    return {
        "source_count": len([source for source in registry.sources if source.enabled]),
        "chunk_count": len(rows),
        "question_count": len(question_payload["questions"]),
        "evidence_group_count": sum(
            len(question["evidence_groups"])
            for question in question_payload["questions"]
        ),
        "ground_truth_valid": not missing,
        "missing_evidence_groups": missing,
    }


def score_result(
    result: dict[str, Any],
    question: dict[str, Any],
) -> dict[str, Any]:
    rows = result["evidence"]
    group_results = score_groups(rows, question)
    evidence_hit = sum(item["rank"] is not None for item in group_results)
    evidence_total = len(group_results)
    expected_projects = {group["project_id"] for group in question["evidence_groups"]}
    retrieved_projects = {row["project_id"] for row in rows}
    project_hit = len(expected_projects & retrieved_projects)
    traceable = sum(
        bool(
            row.get("source_id")
            and row.get("relative_path")
            and row.get("heading")
            and row.get("source_text")
        )
        for row in rows
    )
    record = {
        "question_id": question["id"],
        "query": question["query"],
        "complex_query": result["complex_query"],
        "evidence_hit": evidence_hit,
        "evidence_total": evidence_total,
        "evidence_coverage": round(evidence_hit / evidence_total, 4),
        "full_evidence_coverage": evidence_hit == evidence_total,
        "project_hit": project_hit,
        "project_total": len(expected_projects),
        "project_coverage": round(project_hit / len(expected_projects), 4),
        "traceable_evidence": traceable,
        "evidence_count": len(rows),
        "group_results": group_results,
        "forbidden_conclusions": question.get("forbidden_conclusions", []),
        "evidence": rows,
    }
    candidate_rows = result.get("candidate_evidence")
    if isinstance(candidate_rows, list):
        candidate_group_results = score_groups(candidate_rows, question)
        candidate_hit = sum(
            item["rank"] is not None for item in candidate_group_results
        )
        candidate_projects = {row["project_id"] for row in candidate_rows}
        candidate_project_hit = len(expected_projects & candidate_projects)
        record.update(
            {
                "candidate_evidence_hit": candidate_hit,
                "candidate_evidence_total": evidence_total,
                "candidate_evidence_coverage": round(
                    candidate_hit / evidence_total,
                    4,
                ),
                "candidate_project_hit": candidate_project_hit,
                "candidate_project_total": len(expected_projects),
                "candidate_project_coverage": round(
                    candidate_project_hit / len(expected_projects),
                    4,
                ),
                "candidate_count": len(candidate_rows),
                "candidate_group_results": candidate_group_results,
            }
        )
    return record


def score_groups(
    rows: list[dict[str, Any]],
    question: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "id": group["id"],
            "project_id": group["project_id"],
            "description": group["description"],
            "markers": group["markers"],
            "rank": group_rank(rows, group),
        }
        for group in question["evidence_groups"]
    ]


def summarize(
    records: list[dict[str, Any]],
    latency_samples: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_hit = sum(record["evidence_hit"] for record in records)
    evidence_total = sum(record["evidence_total"] for record in records)
    project_hit = sum(record["project_hit"] for record in records)
    project_total = sum(record["project_total"] for record in records)
    evidence_returned = sum(record["evidence_count"] for record in records)
    traceable = sum(record["traceable_evidence"] for record in records)
    has_candidates = all("candidate_evidence_hit" in record for record in records)

    def latency_summary(complex_query: bool) -> dict[str, float | int]:
        values = [
            sample["latency_ms"]
            for sample in latency_samples
            if sample["complex_query"] is complex_query
        ]
        return {
            "samples": len(values),
            "median_ms": round(statistics.median(values), 3) if values else 0.0,
            "p95_ms": round(percentile(values, 0.95), 3),
            "mean_ms": round(statistics.mean(values), 3) if values else 0.0,
        }

    evidence_coverage = evidence_hit / evidence_total
    project_coverage = project_hit / project_total
    traceability = traceable / evidence_returned if evidence_returned else 0.0
    simple_latency = latency_summary(False)
    complex_latency = latency_summary(True)
    summary = {
        "questions": len(records),
        "full_evidence_questions": sum(
            record["full_evidence_coverage"] for record in records
        ),
        "evidence_groups_hit": evidence_hit,
        "evidence_groups_total": evidence_total,
        "evidence_coverage": round(evidence_coverage, 4),
        "projects_hit": project_hit,
        "projects_total": project_total,
        "project_coverage": round(project_coverage, 4),
        "traceable_evidence": traceable,
        "evidence_returned": evidence_returned,
        "traceability": round(traceability, 4),
        "latency_simple": simple_latency,
        "latency_complex": complex_latency,
        "acceptance": {
            "evidence_coverage_gte_85pct": evidence_coverage >= 0.85,
            "project_coverage_gte_95pct": project_coverage >= 0.95,
            "traceability_100pct": traceability == 1.0,
            "simple_p95_lte_500ms": (
                simple_latency["samples"] == 0 or float(simple_latency["p95_ms"]) <= 500
            ),
            "complex_p95_lte_1200ms": (
                complex_latency["samples"] == 0
                or float(complex_latency["p95_ms"]) <= 1200
            ),
        },
    }
    if has_candidates:
        candidate_hit = sum(record["candidate_evidence_hit"] for record in records)
        candidate_total = sum(record["candidate_evidence_total"] for record in records)
        candidate_project_hit = sum(
            record["candidate_project_hit"] for record in records
        )
        candidate_project_total = sum(
            record["candidate_project_total"] for record in records
        )
        candidate_coverage = candidate_hit / candidate_total
        candidate_project_coverage = candidate_project_hit / candidate_project_total
        summary["candidate_stage"] = {
            "evidence_groups_hit": candidate_hit,
            "evidence_groups_total": candidate_total,
            "evidence_coverage": round(candidate_coverage, 4),
            "projects_hit": candidate_project_hit,
            "projects_total": candidate_project_total,
            "project_coverage": round(candidate_project_coverage, 4),
        }
        summary["acceptance"].update(
            {
                "candidate_evidence_coverage_gte_95pct": (candidate_coverage >= 0.95),
                "candidate_project_coverage_100pct": (
                    candidate_project_coverage == 1.0
                ),
                "reranked_evidence_coverage_gte_90pct": (evidence_coverage >= 0.90),
            }
        )
    return summary


def run_benchmark(
    service: KnowledgeService,
    question_bytes: bytes,
    question_payload: dict[str, Any],
    *,
    mode: str,
    top_k: int | None,
    repeats: int,
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    records: list[dict[str, Any]] = []
    latency_samples: list[dict[str, Any]] = []
    for question in question_payload["questions"]:
        first_result = None
        for repeat in range(1, repeats + 1):
            started = time.perf_counter()
            result = service.search(
                question["query"],
                mode=mode,
                top_k=top_k,
                include_candidates=mode == "quality" and repeat == 1,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            latency_samples.append(
                {
                    "question_id": question["id"],
                    "repeat": repeat,
                    "complex_query": result["complex_query"],
                    "latency_ms": round(latency_ms, 3),
                }
            )
            if first_result is None:
                first_result = result
        records.append(score_result(first_result, question))
    summary = summarize(records, latency_samples)
    if mode == "recall":
        summary["acceptance"].update(
            {
                "recall_evidence_coverage_gte_95pct": (
                    summary["evidence_coverage"] >= 0.95
                ),
                "recall_project_coverage_100pct": (summary["project_coverage"] == 1.0),
            }
        )
    return attach_corpus_snapshot({
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": service.settings.embedding.model,
        "embedding_fingerprint": service.embedder.fingerprint,
        "reranker_fingerprint": (
            service.retriever.reranker.fingerprint
            if service.retriever.reranker is not None
            else None
        ),
        "question_count": len(question_payload["questions"]),
        "questions_sha256": hashlib.sha256(question_bytes).hexdigest(),
        "evaluation_corrections": question_payload.get("_evaluation_corrections"),
        "mode": mode,
        "top_k": top_k,
        "repeats": repeats,
        "index_version": service.state.index_version(),
        "summary": summary,
        "latency_samples": latency_samples,
        "records": records,
    }, service)


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
