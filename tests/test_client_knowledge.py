from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_incremental_service import FakeEmbedder, markdown

from ja_local_kb.models import (
    AppSettings,
    ChunkingConfig,
    EmbeddingConfig,
    SourceRegistry,
)
from ja_local_kb.registry import (
    load_registry,
    make_client_source_spec,
    make_source_spec,
    write_registry,
)
from ja_local_kb.service import KnowledgeService

CLIENT_DOCUMENTS = (
    ("client_overview", "00_客户入口"),
    ("client_facts", "01_客户事实与组织业务"),
    ("client_rule_library", "02_客户偏好禁忌与执行规则"),
    ("client_decision_log", "03_客户决策与转折点"),
    ("client_experience_library", "04_客户经验与案例"),
    ("client_change_log", "05_客户变化与验证记录"),
    ("client_source_index", "06_客户来源与证据索引"),
)


def client_markdown(doc_type: str, title: str) -> str:
    return f"""---
doc_type: {doc_type}
client: 虚构客户有限公司
client_short_name: EXAMPLEA
client_id: example-a
status: active
owner: Test
created: 2026-08-10
updated: 2026-08-10
version: v1
tags: [test]
aliases: [EXAMPLEA｜{title}]
related: []
---

# EXAMPLEA｜{title}

## 虚构客户共同证据

EXAMPLEA 的 {title} 当前有效，只用于隔离验收。
"""


class ClientKnowledgeAcceptanceTests(unittest.TestCase):
    def test_isolated_client_lifecycle_restores_project_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "isolated-vault"
            runtime = root / "runtime"
            project_path = vault / "project" / "overview.md"
            project_path.parent.mkdir(parents=True)
            project_path.write_text(markdown(), encoding="utf-8")
            project_source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="project/overview.md",
            )
            registry_path = root / "sources.json"
            write_registry(
                registry_path,
                SourceRegistry(schema_version=1, sources=[project_source]),
            )
            baseline_registry = registry_path.read_bytes()
            settings = AppSettings(
                vault_root=vault,
                runtime_root=runtime,
                source_registry=registry_path,
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="unused-in-test",
                ),
                chunking=ChunkingConfig(max_chars=500),
            )
            baseline_service = KnowledgeService(settings, FakeEmbedder())
            baseline_service.sync_all()
            baseline_project = baseline_service.search(
                "Alpha",
                mode="hybrid",
                project_ids=["project-a"],
            )

            client_sources = []
            client_root = vault / "clients" / "example-a"
            client_root.mkdir(parents=True)
            for document_role, title in CLIENT_DOCUMENTS:
                relative_path = f"clients/example-a/EXAMPLEA｜{title}.md"
                path = vault / relative_path
                path.write_text(
                    client_markdown(document_role, title),
                    encoding="utf-8",
                )
                client_sources.append(
                    make_client_source_spec(
                        client_id="example-a",
                        client_name="EXAMPLEA",
                        document_role=document_role,
                        relative_path=relative_path,
                    )
                )
            archive_path = client_root / "80_archive" / "EXAMPLEA｜80_客户历史归档.md"
            archive_path.parent.mkdir()
            archive_path.write_text(
                client_markdown("client_archive", "80_客户历史归档"),
                encoding="utf-8",
            )
            untouched_path = vault / client_sources[0].relative_path
            untouched_markdown = untouched_path.read_bytes()

            write_registry(
                registry_path,
                SourceRegistry(
                    schema_version=1,
                    sources=[project_source, *client_sources],
                ),
            )
            service = KnowledgeService(settings, FakeEmbedder())
            service.sync_all()
            registered = load_registry(registry_path).sources
            self.assertEqual(
                len(
                    [
                        source
                        for source in registered
                        if source.client_id == "example-a"
                    ]
                ),
                7,
            )
            self.assertEqual(
                len(
                    [
                        source
                        for source in registered
                        if source.document_role == "client_archive"
                        or "80_archive" in source.relative_path
                    ]
                ),
                0,
            )
            self.assertEqual(untouched_path.read_bytes(), untouched_markdown)
            self.assertTrue(service.status()["ready"])

            client_result = service.search(
                "虚构客户共同证据",
                mode="recall",
                client_ids=["example-a"],
            )
            self.assertTrue(client_result["evidence"])
            self.assertTrue(
                all(
                    evidence["client_id"] == "example-a"
                    for evidence in client_result["evidence"]
                )
            )
            readback = service.get_source(source_id=client_sources[2].source_id)
            self.assertEqual(readback["client_id"], "example-a")
            self.assertIn("EXAMPLEA｜02_客户偏好禁忌与执行规则", readback["content"])

            changed_path = vault / client_sources[2].relative_path
            changed_path.write_text(
                changed_path.read_text(encoding="utf-8") + "\n更新后的客户规则。\n",
                encoding="utf-8",
            )
            stale = service.status()
            self.assertFalse(stale["ready"])
            self.assertEqual(
                next(
                    source
                    for source in stale["blocking_sources"]
                    if source["source_id"] == client_sources[2].source_id
                )["status"],
                "stale",
            )
            service.sync_source(client_sources[2].source_id)
            self.assertTrue(service.status()["ready"])

            project_with_clients = service.search(
                "Alpha",
                mode="hybrid",
                project_ids=["project-a"],
            )
            self.assertEqual(
                [item["evidence_id"] for item in project_with_clients["evidence"]],
                [item["evidence_id"] for item in baseline_project["evidence"]],
            )

            for source in client_sources:
                service.remove_source(source.source_id)
            restored = service.status()
            self.assertTrue(restored["ready"])
            self.assertEqual(restored["enabled_source_count"], 1)
            self.assertEqual(registry_path.read_bytes(), baseline_registry)
            restored_project = service.search(
                "Alpha",
                mode="hybrid",
                project_ids=["project-a"],
            )
            self.assertEqual(
                [item["evidence_id"] for item in restored_project["evidence"]],
                [item["evidence_id"] for item in baseline_project["evidence"]],
            )


if __name__ == "__main__":
    unittest.main()
