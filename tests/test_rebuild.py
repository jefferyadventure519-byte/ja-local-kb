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
from ja_local_kb.rebuild import rebuild_index
from ja_local_kb.registry import make_source_spec, write_registry
from ja_local_kb.service import KnowledgeService


class RebuildTests(unittest.TestCase):
    def test_rebuild_swaps_index_and_keeps_recoverable_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            runtime = root / "runtime"
            state = root / "state" / "state.sqlite3"
            lancedb = root / "data" / "lancedb"
            (vault / "project").mkdir(parents=True)
            source_path = vault / "project" / "overview.md"
            source_path.write_text(markdown(), encoding="utf-8")
            source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="project/overview.md",
            )
            registry = root / "sources.json"
            write_registry(
                registry,
                SourceRegistry(schema_version=1, sources=[source]),
            )
            settings = AppSettings(
                vault_root=vault,
                runtime_root=runtime,
                source_registry=registry,
                state_path=state,
                lancedb_root=lancedb,
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="unused-in-test",
                ),
                chunking=ChunkingConfig(max_chars=500),
            )
            first_service = KnowledgeService(settings, FakeEmbedder())
            first_service.sync_all()
            previous_version = first_service.state.index_version()
            source_path.write_text(
                markdown("Rebuilt beta."),
                encoding="utf-8",
            )
            result = rebuild_index(settings, FakeEmbedder())
            self.assertTrue(result["rebuilt"])
            self.assertGreater(result["index_version"], previous_version)
            self.assertTrue(Path(result["backup_root"]).is_dir())
            service = KnowledgeService(settings, FakeEmbedder())
            self.assertTrue(service.status()["ready"])
            evidence = service.search(
                "Rebuilt",
                mode="keyword",
                top_k=1,
            )["evidence"]
            self.assertEqual(len(evidence), 1)


if __name__ == "__main__":
    unittest.main()
