from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ja_local_kb.benchmark import (
    group_rank,
    load_questions,
    percentile,
    score_result,
    summarize,
)


def test_percentile_interpolates() -> None:
    assert percentile([100.0, 200.0, 300.0], 0.5) == 200.0
    assert percentile([], 0.95) == 0.0


def test_score_and_summary_use_traceable_marker_hits() -> None:
    group = {
        "id": "g1",
        "project_id": "project-a",
        "description": "decision",
        "markers": ["Alpha", "Beta"],
    }
    evidence = [
        {
            "source_id": "source-a",
            "project_id": "project-a",
            "relative_path": "project/02.md",
            "heading": "Decision",
            "source_text": "Alpha and Beta",
        }
    ]
    assert group_rank(evidence, group) == 1
    record = score_result(
        {
            "complex_query": True,
            "evidence": evidence,
        },
        {
            "id": "q1",
            "query": "query",
            "evidence_groups": [group],
            "forbidden_conclusions": [],
        },
    )
    summary = summarize(
        [record],
        [
            {
                "question_id": "q1",
                "repeat": 1,
                "complex_query": True,
                "latency_ms": 800.0,
            }
        ],
    )
    assert summary["evidence_coverage"] == 1.0
    assert summary["project_coverage"] == 1.0
    assert summary["traceability"] == 1.0
    assert summary["acceptance"]["complex_p95_lte_1200ms"]


def test_quality_summary_reports_candidate_and_reranked_coverage() -> None:
    groups = [
        {
            "id": "g1",
            "project_id": "project-a",
            "description": "first",
            "markers": ["Alpha"],
        },
        {
            "id": "g2",
            "project_id": "project-b",
            "description": "second",
            "markers": ["Beta"],
        },
    ]
    candidate_evidence = [
        {
            "source_id": "source-a",
            "project_id": "project-a",
            "relative_path": "project-a/02.md",
            "heading": "Alpha decision",
            "source_text": "Alpha",
        },
        {
            "source_id": "source-b",
            "project_id": "project-b",
            "relative_path": "project-b/03.md",
            "heading": "Beta rule",
            "source_text": "Beta",
        },
    ]
    record = score_result(
        {
            "complex_query": True,
            "evidence": candidate_evidence[:1],
            "candidate_evidence": candidate_evidence,
        },
        {
            "id": "q1",
            "query": "query",
            "evidence_groups": groups,
        },
    )
    summary = summarize(
        [record],
        [
            {
                "question_id": "q1",
                "repeat": 1,
                "complex_query": True,
                "latency_ms": 1500.0,
            }
        ],
    )
    assert summary["candidate_stage"]["evidence_coverage"] == 1.0
    assert summary["candidate_stage"]["project_coverage"] == 1.0
    assert summary["evidence_coverage"] == 0.5
    assert summary["acceptance"]["candidate_evidence_coverage_gte_95pct"]
    assert not summary["acceptance"]["reranked_evidence_coverage_gte_90pct"]


def test_load_questions_applies_hash_bound_correction(tmp_path: Path) -> None:
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "q1",
                        "query": "query",
                        "evidence_groups": [
                            {
                                "id": "g1",
                                "project_id": "project-a",
                                "description": "decision",
                                "markers": ["old"],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    source_hash = hashlib.sha256(questions_path.read_bytes()).hexdigest()
    corrections_path = tmp_path / "corrections.json"
    corrections_path.write_text(
        json.dumps(
            {
                "source_questions_sha256": source_hash,
                "corrections": [
                    {
                        "question_id": "q1",
                        "group_id": "g1",
                        "previous_markers": ["old"],
                        "current_markers": ["current"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    effective_bytes, payload = load_questions(questions_path, corrections_path)

    assert payload["questions"][0]["evidence_groups"][0]["markers"] == ["current"]
    assert payload["_evaluation_corrections"]["source_questions_sha256"] == source_hash
    assert hashlib.sha256(effective_bytes).hexdigest() != source_hash


def test_load_questions_rejects_correction_for_different_source(
    tmp_path: Path,
) -> None:
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "id": "q1",
                        "query": "query",
                        "evidence_groups": [
                            {
                                "id": "g1",
                                "project_id": "project-a",
                                "description": "decision",
                                "markers": ["old"],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    corrections_path = tmp_path / "corrections.json"
    corrections_path.write_text(
        json.dumps(
            {
                "source_questions_sha256": "0" * 64,
                "corrections": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source hash"):
        load_questions(questions_path, corrections_path)
