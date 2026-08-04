from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from test_incremental_service import FakeEmbedder, markdown

from ja_local_kb.models import (
    AppSettings,
    ChunkingConfig,
    EmbeddingConfig,
    SourceRegistry,
)
from ja_local_kb.multi_route import (
    CROSS_DEVICE_TRACE_QUERY,
    GOVERNANCE_BOUNDARY_QUERY,
    _quotas,
    decompose_query,
)
from ja_local_kb.registry import make_source_spec, write_registry
from ja_local_kb.reranker import RerankResult
from ja_local_kb.retrieval import Retriever, plan_search
from ja_local_kb.service import KnowledgeService


class FakeReranker:
    fingerprint = "fake:reranker"

    def __init__(self) -> None:
        self.calls = []

    def rerank(self, query, documents, *, top_n):
        self.calls.append((query, documents, top_n))
        return [
            RerankResult(index=index, score=1.0 - index / 100) for index in range(top_n)
        ]


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.vault = root / "vault"
        self.runtime = root / "runtime"
        (self.vault / "project").mkdir(parents=True)
        (self.vault / "project" / "overview.md").write_text(
            markdown(),
            encoding="utf-8",
        )
        self.source = make_source_spec(
            project_id="project-a",
            project_name="Project A",
            document_role="project_overview",
            relative_path="project/overview.md",
        )
        registry = root / "sources.json"
        write_registry(
            registry,
            SourceRegistry(schema_version=1, sources=[self.source]),
        )
        self.settings = AppSettings(
            vault_root=self.vault,
            runtime_root=self.runtime,
            source_registry=registry,
            embedding=EmbeddingConfig(
                provider="openai",
                model="unused-in-test",
            ),
            chunking=ChunkingConfig(max_chars=500),
        )
        self.embedder = FakeEmbedder()
        self.service = KnowledgeService(self.settings, self.embedder)
        self.service.sync_all()

    def test_keyword_search_returns_traceable_evidence(self) -> None:
        result = self.service.search("Alpha", mode="keyword", top_k=3)
        self.assertEqual(result["freshness"], "fresh")
        self.assertGreaterEqual(result["evidence_count"], 1)
        evidence = result["evidence"][0]
        self.assertTrue(evidence["evidence_id"].startswith("ev_"))
        self.assertEqual(evidence["source_id"], self.source.source_id)
        self.assertEqual(evidence["relative_path"], "project/overview.md")
        self.assertEqual(evidence["freshness"], "fresh")

    def test_hybrid_search_and_project_filter(self) -> None:
        result = self.service.search(
            "Alpha",
            mode="hybrid",
            top_k=3,
            project_ids=["project-a"],
        )
        self.assertGreaterEqual(result["evidence_count"], 1)
        self.assertTrue(
            all(item["project_id"] == "project-a" for item in result["evidence"])
        )
        empty = self.service.search(
            "Alpha",
            mode="keyword",
            top_k=3,
            project_ids=["other-project"],
        )
        self.assertEqual(empty["evidence"], [])

    def test_get_source_never_accepts_a_path(self) -> None:
        result = self.service.search("Alpha", mode="keyword", top_k=1)
        evidence_id = result["evidence"][0]["evidence_id"]
        evidence = self.service.get_source(evidence_id=evidence_id)
        self.assertEqual(evidence["kind"], "evidence")
        source = self.service.get_source(source_id=self.source.source_id)
        self.assertEqual(source["kind"], "source")
        with self.assertRaises((KeyError, ValueError)):
            self.service.get_source(source_id="project/overview.md")

    def test_smart_mode_expands_complex_query(self) -> None:
        plan = plan_search(
            "请对比多个项目的共同决策与冲突",
            self.settings.retrieval,
            mode="smart",
            top_k=None,
        )
        self.assertTrue(plan.complex_query)
        self.assertEqual(plan.executed_mode, "hybrid")
        self.assertEqual(plan.top_k, self.settings.retrieval.complex_top_k)
        self.assertEqual(plan.candidate_k, self.settings.retrieval.smart_candidate_k)

    def test_query_decomposition_anchors_specific_clauses(self) -> None:
        query = "客户知识和项目状态应该放在哪里？哪些内容必须保持分离？它们如何关联？"
        subqueries = decompose_query(query)
        self.assertEqual(subqueries[0], query)
        self.assertEqual(len(subqueries), 4)
        self.assertTrue(all("客户知识和项目状态" in item for item in subqueries[1:]))

    def test_project_quotas_prioritize_primary_route(self) -> None:
        quotas = _quotas(["p1", "p2", "p3", "p4", "p5"], top_k=24)
        self.assertEqual(sum(quotas.values()), 24)
        self.assertEqual(quotas, {"p1": 15, "p2": 5, "p3": 2, "p4": 1, "p5": 1})

    def test_query_decomposition_splits_enumerated_focuses(self) -> None:
        query = "能否统一管理客户知识、个人项目、会议资料和政府扫标？哪些必须分离？"
        subqueries = decompose_query(query, max_subqueries=6)
        self.assertEqual(len(subqueries), 6)
        self.assertIn(GOVERNANCE_BOUNDARY_QUERY, subqueries)
        self.assertIn("能否统一管理客户知识；哪些必须分离", subqueries)
        self.assertIn("能否统一管理个人项目；哪些必须分离", subqueries)
        self.assertIn("能否统一管理会议资料；哪些必须分离", subqueries)
        self.assertIn("能否统一管理政府扫标；哪些必须分离", subqueries)

    def test_query_decomposition_adds_cross_device_traceability_guard(self) -> None:
        subqueries = decompose_query(
            "本地文件路径在其他电脑打不开，如何保证来源可追溯且不假装同步？",
            max_subqueries=6,
        )
        self.assertIn(CROSS_DEVICE_TRACE_QUERY, subqueries)

    def test_query_decomposition_adds_write_safety_guard(self) -> None:
        subqueries = decompose_query(
            "能不能自动写进知识库？现行安全边界是什么？",
            max_subqueries=5,
        )
        self.assertIn(
            "写入流程；preview；人工确认；权限；审批；回滚；安全边界",
            subqueries,
        )

    def test_smart_complex_search_batches_subqueries(self) -> None:
        query = "Alpha 当前是什么？哪些规则需要保留？它们如何关联？"
        result = self.service.search(query, mode="smart")
        self.assertEqual(
            result["retrieval_strategy"],
            "deterministic_multi_route_v5",
        )
        self.assertGreater(len(result["subqueries"]), 1)
        expected_vector_queries = min(
            len(result["subqueries"]),
            self.settings.retrieval.smart_vector_subqueries,
        )
        self.assertEqual(
            len(self.embedder.calls[-1]),
            expected_vector_queries,
        )
        self.assertEqual(
            len(result["vector_subqueries"]),
            expected_vector_queries,
        )
        self.assertLessEqual(
            result["evidence_count"],
            self.settings.retrieval.complex_top_k,
        )
        self.assertTrue(result["routed_projects"])

    def test_quality_mode_reranks_high_recall_candidates(self) -> None:
        reranker = FakeReranker()
        service = KnowledgeService(self.settings, self.embedder, reranker)
        query = "Alpha 当前是什么？哪些规则需要保留？它们如何关联？"
        result = service.search(
            query,
            mode="quality",
            top_k=1,
            include_candidates=True,
        )
        self.assertEqual(result["retrieval_strategy"], "high_recall_rerank_v1")
        self.assertEqual(result["reranker_model"], reranker.fingerprint)
        self.assertEqual(result["evidence_count"], 1)
        self.assertGreaterEqual(result["candidate_count"], 1)
        self.assertEqual(
            len(result["candidate_evidence"]),
            result["candidate_count"],
        )
        self.assertEqual(result["evidence"][0]["rerank_rank"], 1)
        self.assertEqual(result["evidence"][0]["candidate_rank"], 1)
        self.assertEqual(len(reranker.calls), 1)
        self.assertIn("Independent evidence needs", reranker.calls[0][0])

    def test_recall_mode_returns_candidate_pool_without_reranker(self) -> None:
        result = self.service.search(
            "Alpha 当前是什么？哪些规则需要保留？它们如何关联？",
            mode="recall",
        )
        self.assertEqual(
            result["retrieval_strategy"],
            "high_recall_candidate_v1",
        )
        self.assertEqual(result["evidence_profile"], "complete_candidate_pool")
        self.assertEqual(result["candidate_count"], result["evidence_count"])
        self.assertTrue(
            all(item["candidate_rank"] is not None for item in result["evidence"])
        )
        self.assertTrue(all(item["rerank_rank"] is None for item in result["evidence"]))

    def test_recall_plan_keeps_configured_candidate_limit(self) -> None:
        plan = plan_search(
            "请对比多个项目的共同决策与冲突",
            self.settings.retrieval,
            mode="recall",
            top_k=None,
        )
        self.assertEqual(plan.top_k, self.settings.retrieval.recall_candidate_k)
        self.assertEqual(
            plan.candidate_k,
            self.settings.retrieval.recall_candidate_k,
        )

    def test_quality_mode_fails_without_reranker(self) -> None:
        with self.assertRaisesRegex(ValueError, "configured reranker"):
            self.service.search("Alpha", mode="quality", top_k=1)

    def test_candidates_are_only_exposed_in_quality_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "only available in quality"):
            self.service.search(
                "Alpha",
                mode="smart",
                include_candidates=True,
            )

    def test_quality_selection_skips_redundant_heading_chunks(self) -> None:
        candidates = [
            SimpleNamespace(
                row={
                    "source_id": "source-a",
                    "project_id": "project-a",
                    "relative_path": "project/overview.md",
                    "heading": "Repeated" if index < 5 else f"Unique {index}",
                }
            )
            for index in range(8)
        ]
        ranked = [
            RerankResult(index=index, score=1.0 - index / 100) for index in range(8)
        ]
        selected = Retriever._select_diverse_ranked(
            candidates,
            ranked,
            top_k=3,
        )
        self.assertEqual([item.index for item in selected], [0, 5, 6])

    def test_quality_selection_keeps_each_routed_project(self) -> None:
        candidates = [
            SimpleNamespace(
                row={
                    "source_id": f"source-{index}",
                    "project_id": "project-a" if index < 4 else "project-b",
                    "relative_path": f"project/{index}.md",
                    "heading": f"Heading {index}",
                }
            )
            for index in range(5)
        ]
        ranked = [
            RerankResult(index=index, score=1.0 - index / 100) for index in range(5)
        ]
        selected = Retriever._select_diverse_ranked(
            candidates,
            ranked,
            top_k=3,
            routed_projects=["project-a", "project-b"],
        )
        self.assertEqual([item.index for item in selected], [0, 1, 4])

    def test_disabled_source_rows_cannot_leak_into_results(self) -> None:
        old_result = self.service.search("Beta", mode="keyword", top_k=1)
        old_evidence_id = old_result["evidence"][0]["evidence_id"]
        (self.vault / "project" / "other.md").write_text(
            markdown("Gamma only.")
            .replace("project_id: project-a", "project_id: project-b")
            .replace("project: Project A", "project: Project B"),
            encoding="utf-8",
        )
        other = make_source_spec(
            project_id="project-b",
            project_name="Project B",
            document_role="project_overview",
            relative_path="project/other.md",
        )
        disabled = self.source.model_copy(update={"enabled": False})
        write_registry(
            self.settings.source_registry,
            SourceRegistry(schema_version=1, sources=[disabled, other]),
        )
        service = KnowledgeService(self.settings, FakeEmbedder())
        service.sync_source(other.source_id)
        result = service.search("Beta", mode="keyword", top_k=3)
        self.assertEqual(result["evidence"], [])
        self.assertTrue(service.status()["ready"])
        with self.assertRaises(KeyError):
            service.get_source(evidence_id=old_evidence_id)
        with self.assertRaises(KeyError):
            service.get_source(source_id=self.source.source_id)


if __name__ == "__main__":
    unittest.main()
