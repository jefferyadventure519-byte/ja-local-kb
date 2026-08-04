from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ja_local_kb.models import ChunkingConfig, RetrievalConfig, SourceSpec
from ja_local_kb.quality_report import (
    build_quality_report,
    current_corpus_snapshot,
)


class FakeState:
    def __init__(self, count: int, index_version: int) -> None:
        self.count = count
        self.version = index_version

    def list_sources(self, source_ids: set[str]) -> list[dict]:
        return [
            {
                "source_id": source_id,
                "committed_hash": f"hash-{source_id}",
                "chunk_count": 2,
                "status": "fresh",
            }
            for source_id in sorted(source_ids)
        ]

    def index_version(self) -> int:
        return self.version


def fake_service(source_count: int = 49, index_version: int = 3304):
    sources = [
        SourceSpec(
            source_id=f"{index:032x}",
            project_id=f"project-{index // 7}",
            project_name=f"项目 {index // 7}",
            document_role="overview",
            relative_path=f"project-{index // 7}/{index}.md",
        )
        for index in range(1, source_count + 1)
    ]
    settings = SimpleNamespace(
        embedding=SimpleNamespace(
            fingerprint=(
                "openai_compatible:https://api.siliconflow.cn/v1:"
                "Qwen/Qwen3-Embedding-8B:4096"
            )
        ),
        chunking=ChunkingConfig(max_chars=2200, overlap_chars=120),
        retrieval=RetrievalConfig(recall_candidate_k=80),
    )
    return SimpleNamespace(
        registry=SimpleNamespace(sources=sources),
        state=FakeState(source_count, index_version),
        settings=settings,
    )


def test_builtin_formal_report_is_explicitly_outdated_for_changed_corpus() -> None:
    report = build_quality_report(fake_service())
    assert report["status"] == "outdated"
    assert "纳入文档数已变化" in report["comparison"]["mismatch_reasons"]
    assert "索引版本已变化" in report["comparison"]["mismatch_reasons"]
    assert report["benchmark"]["summary"]["evidence_groups_hit"] == 47
    assert report["current"]["source_count"] == 49
    assert "不能代表当前知识库" in report["verdict"]


def test_fingerprinted_current_report_is_marked_current(tmp_path: Path) -> None:
    service = fake_service(source_count=3, index_version=8)
    snapshot = current_corpus_snapshot(service)
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            {
                "created_at": "2026-08-01T10:00:00+08:00",
                "question_count": 1,
                "questions_sha256": "abc",
                "mode": "recall",
                "top_k": 80,
                "repeats": 1,
                "corpus_snapshot": snapshot,
                "summary": {
                    "questions": 1,
                    "evidence_groups_hit": 1,
                    "evidence_groups_total": 1,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = build_quality_report(service, report_path=path)
    assert report["status"] == "current"
    assert report["comparison"]["mismatch_reasons"] == []
    assert report["comparison"]["unverifiable_reasons"] == []


def test_legacy_report_without_snapshot_is_unverified(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps({"summary": {"questions": 12}}),
        encoding="utf-8",
    )
    report = build_quality_report(fake_service(), report_path=path)
    assert report["status"] == "unverified"
    assert report["comparison"]["unverifiable_reasons"]
