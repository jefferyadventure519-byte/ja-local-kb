"""Bounded recall-quality reporting with explicit corpus freshness checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .service import KnowledgeService

# Compact, non-sensitive summary of the frozen formal result. The complete result
# remains outside the distributable package because it contains source excerpts.
BUILTIN_BASELINE: dict[str, Any] = {
    "schema_version": 1,
    "label": "2026-07-30 交叉项目高召回正式基准",
    "created_at": "2026-07-30T15:40:40+08:00",
    "provenance": "20260730_recall_v1_8bembed_top80_r1.json",
    "question_count": 12,
    "questions_sha256": (
        "aad6eb7c4897e0e27198fd430d2927582f8e543ac3e6fa6688af7c76e64edc1b"
    ),
    "mode": "recall",
    "top_k": 80,
    "repeats": 1,
    "corpus_snapshot": {
        "source_count": 42,
        "chunk_count": 783,
        "index_version": 131,
        "embedding_fingerprint": (
            "openai_compatible:https://api.siliconflow.cn/v1:"
            "Qwen/Qwen3-Embedding-8B:4096"
        ),
        "chunking": {"max_chars": 2200, "overlap_chars": 120},
        "retrieval": {"mode": "recall", "top_k": 80},
    },
    "summary": {
        "questions": 12,
        "full_evidence_questions": 12,
        "evidence_groups_hit": 47,
        "evidence_groups_total": 47,
        "evidence_coverage": 1.0,
        "projects_hit": 27,
        "projects_total": 27,
        "project_coverage": 1.0,
        "traceable_evidence": 960,
        "evidence_returned": 960,
        "traceability": 1.0,
        "latency_complex": {
            "samples": 12,
            "median_ms": 3111.959,
            "p95_ms": 35913.513,
            "mean_ms": 9609.522,
        },
    },
}


def current_corpus_snapshot(service: KnowledgeService) -> dict[str, Any]:
    registry_sources = [source for source in service.registry.sources if source.enabled]
    state_rows = {
        row["source_id"]: row
        for row in service.state.list_sources(
            {source.source_id for source in registry_sources}
        )
    }
    identity_rows = []
    for source in sorted(registry_sources, key=lambda item: item.source_id):
        row = state_rows.get(source.source_id, {})
        identity_rows.append(
            {
                "source_id": source.source_id,
                "project_id": source.project_id,
                "relative_path": source.relative_path,
                "document_role": source.document_role,
                "committed_hash": row.get("committed_hash", ""),
                "chunk_count": int(row.get("chunk_count", 0)),
                "status": row.get("status", "not_indexed"),
            }
        )
    fingerprint_payload = {
        "sources": identity_rows,
        "chunking": service.settings.chunking.model_dump(mode="json"),
        "embedding_fingerprint": service.settings.embedding.fingerprint,
    }
    corpus_sha256 = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "source_count": len(registry_sources),
        "chunk_count": sum(int(row.get("chunk_count", 0)) for row in identity_rows),
        "index_version": service.state.index_version(),
        "embedding_fingerprint": service.settings.embedding.fingerprint,
        "chunking": service.settings.chunking.model_dump(mode="json"),
        "retrieval": {
            "mode": "recall",
            "top_k": service.settings.retrieval.recall_candidate_k,
        },
        "corpus_sha256": corpus_sha256,
    }


def attach_corpus_snapshot(
    result: dict[str, Any],
    service: KnowledgeService,
) -> dict[str, Any]:
    result["corpus_snapshot"] = current_corpus_snapshot(service)
    return result


def load_benchmark_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return json.loads(json.dumps(BUILTIN_BASELINE))
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise ValueError("Quality report must contain a benchmark summary")
    return {
        "schema_version": 1,
        "label": payload.get("label") or path.stem,
        "created_at": payload.get("created_at", ""),
        "provenance": str(path.resolve()),
        "question_count": payload.get("question_count"),
        "questions_sha256": payload.get("questions_sha256", ""),
        "mode": payload.get("mode", ""),
        "top_k": payload.get("top_k"),
        "repeats": payload.get("repeats"),
        "corpus_snapshot": payload.get("corpus_snapshot"),
        "summary": payload["summary"],
    }


def build_quality_report(
    service: KnowledgeService,
    *,
    report_path: Path | None = None,
) -> dict[str, Any]:
    benchmark = load_benchmark_summary(report_path)
    current = current_corpus_snapshot(service)
    reference = benchmark.get("corpus_snapshot")
    reasons: list[str] = []
    unverifiable: list[str] = []
    if not isinstance(reference, dict):
        unverifiable.append("报告缺少语料快照，无法证明结果适用于当前知识库")
    else:
        comparisons = (
            ("source_count", "纳入文档数已变化"),
            ("index_version", "索引版本已变化"),
            ("embedding_fingerprint", "Embedding 模型或配置已变化"),
            ("chunking", "切片配置已变化"),
            ("retrieval", "召回模式或 Top K 已变化"),
        )
        for key, message in comparisons:
            if key in reference and reference[key] != current[key]:
                reasons.append(message)
        expected_hash = reference.get("corpus_sha256")
        if expected_hash:
            if expected_hash != current["corpus_sha256"]:
                reasons.append("语料内容或来源身份已变化")
        else:
            unverifiable.append("历史报告没有内容级语料指纹")
    if reasons:
        status = "outdated"
        verdict = "历史基准已过期，不能代表当前知识库的完整召回水平"
    elif unverifiable:
        status = "unverified"
        verdict = "基准与当前配置未发现显式冲突，但缺少足够指纹，不能证明仍然有效"
    else:
        status = "current"
        verdict = "该正式基准与当前语料、模型、切片和召回配置一致"
    return {
        "status": status,
        "verdict": verdict,
        "scope": "冻结题目与人工标注预期证据范围内的正式召回验证",
        "benchmark": benchmark,
        "current": current,
        "comparison": {
            "mismatch_reasons": reasons,
            "unverifiable_reasons": unverifiable,
        },
        "limitations": [
            "只证明冻结语料、冻结问题和人工标注证据范围内的召回表现。",
            "不能证明任意新问题已经召回所有可能相关内容。",
            "Agent 的最终分析与回答质量不属于该指标。",
            "延迟、证据覆盖、项目覆盖和可追溯性必须分别查看。",
        ],
    }
