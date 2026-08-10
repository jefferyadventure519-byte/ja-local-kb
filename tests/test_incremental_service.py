from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ja_local_kb.errors import (
    EmbeddingUnavailableError,
    FreshnessError,
    IndexIntegrityError,
    NotIndexedError,
)
from ja_local_kb.models import (
    AppSettings,
    ChunkingConfig,
    EmbeddingConfig,
    SourceRegistry,
    SourceStatus,
)
from ja_local_kb.registry import (
    load_registry,
    make_source_spec,
    upsert_source,
    write_registry,
)
from ja_local_kb.service import KnowledgeService


def markdown(second: str = "Beta paragraph.") -> str:
    return f"""---
doc_type: project_overview
project: Project A
project_id: project-a
status: active
owner: Test
created: 2026-07-29
updated: 2026-07-29
version: v1
tags: [test]
aliases: [Overview]
related: []
---

# Overview

## First decision

Alpha paragraph.

## Second decision

{second}
"""


class FakeEmbedder:
    fingerprint = "fake:test:4"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    def embed(self, texts):
        values = list(texts)
        self.calls.append(values)
        if self.fail and values:
            raise EmbeddingUnavailableError("simulated outage")
        vectors = []
        for value in values:
            digest = hashlib.sha256(value.encode("utf-8")).digest()
            vectors.append(
                [
                    float(digest[0]) / 255,
                    float(digest[1]) / 255,
                    float(digest[2]) / 255,
                    float(digest[3]) / 255,
                ]
            )
        return vectors


class IncrementalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        self.runtime = self.root / "runtime"
        self.vault.mkdir()
        (self.vault / "project").mkdir()
        self.path = self.vault / "project" / "overview.md"
        self.path.write_text(markdown(), encoding="utf-8")
        self.registry_path = self.root / "sources.json"
        self.source = make_source_spec(
            project_id="project-a",
            project_name="Project A",
            document_role="project_overview",
            relative_path="project/overview.md",
        )
        self.write_registry(self.source)
        self.settings = AppSettings(
            vault_root=self.vault,
            runtime_root=self.runtime,
            source_registry=self.registry_path,
            embedding=EmbeddingConfig(
                provider="openai",
                model="unused-in-test",
            ),
            chunking=ChunkingConfig(max_chars=500),
        )

    def write_registry(self, source) -> None:
        write_registry(
            self.registry_path,
            SourceRegistry(schema_version=1, sources=[source]),
        )

    def test_registered_unsynced_source_remains_not_indexed(self) -> None:
        service = KnowledgeService(self.settings, FakeEmbedder())
        status = service.status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["counts"], {"not_indexed": 1})
        self.assertEqual(
            status["blocking_sources"][0]["status"],
            "not_indexed",
        )
        service.state.set_status(self.source.source_id, SourceStatus.STALE)
        recovered = service.status()
        self.assertEqual(recovered["counts"], {"not_indexed": 1})

    def test_initial_sync_and_unchanged_resync_reuse_vectors(self) -> None:
        embedder = FakeEmbedder()
        service = KnowledgeService(self.settings, embedder)
        first = service.sync_all()[0]
        self.assertEqual(first["embedded_count"], first["chunk_count"])
        self.assertTrue(service.status()["ready"])

        second = service.sync_all()[0]
        self.assertEqual(second["embedded_count"], 0)
        self.assertEqual(second["reused_count"], second["chunk_count"])
        self.assertEqual(embedder.calls[-1], [])

    def test_only_changed_chunk_is_reembedded(self) -> None:
        embedder = FakeEmbedder()
        service = KnowledgeService(self.settings, embedder)
        first = service.sync_all()[0]
        self.path.write_text(markdown("Changed beta paragraph."), encoding="utf-8")
        with self.assertRaises(FreshnessError):
            service.assert_ready()
        second = service.sync_all()[0]
        self.assertEqual(second["embedded_count"], 1)
        self.assertEqual(second["reused_count"], first["chunk_count"] - 1)
        self.assertTrue(service.assert_ready()["ready"])

    def test_long_lived_reader_refreshes_an_external_index_commit(self) -> None:
        client_source = make_source_spec(
            project_id="project-a",
            project_name="Project A",
            client_id="client-a",
            document_role="project_overview",
            relative_path="project/overview.md",
        )
        self.write_registry(client_source)
        writer = KnowledgeService(self.settings, FakeEmbedder())
        first = writer.sync_all()[0]
        reader = KnowledgeService(self.settings, FakeEmbedder())

        original = reader.search(
            "Beta paragraph",
            mode="hybrid",
            top_k=8,
            client_ids=["client-a"],
        )
        old_evidence = next(
            evidence
            for evidence in original["evidence"]
            if "Beta paragraph." in evidence["source_text"]
        )
        old_evidence_id = old_evidence["evidence_id"]
        # Warm the process-local vector snapshot before the external writer commits.
        reader.search(
            "Beta paragraph",
            mode="vector",
            top_k=8,
            client_ids=["client-a"],
        )

        changed_text = "Changed beta paragraph with cross-process marker."
        self.path.write_text(markdown(changed_text), encoding="utf-8")
        changed = writer.sync_source(client_source.source_id)
        self.assertGreater(changed["index_version"], first["index_version"])

        refreshed = reader.search(
            "cross-process marker",
            mode="hybrid",
            top_k=8,
            client_ids=["client-a"],
        )
        self.assertEqual(refreshed["index_version"], changed["index_version"])
        self.assertTrue(
            any(
                changed_text in evidence["source_text"]
                and evidence["client_id"] == "client-a"
                for evidence in refreshed["evidence"]
            )
        )
        new_evidence_id = next(
            evidence["evidence_id"]
            for evidence in refreshed["evidence"]
            if changed_text in evidence["source_text"]
        )
        vector_refreshed = reader.search(
            "cross-process marker",
            mode="vector",
            top_k=8,
            client_ids=["client-a"],
        )
        self.assertTrue(
            any(
                changed_text in evidence["source_text"]
                for evidence in vector_refreshed["evidence"]
            )
        )
        recall_refreshed = reader.search(
            "cross-process marker",
            mode="recall",
            top_k=8,
            client_ids=["client-a"],
        )
        self.assertTrue(
            any(
                changed_text in evidence["source_text"]
                and evidence["client_id"] == "client-a"
                for evidence in recall_refreshed["evidence"]
            )
        )
        self.assertNotIn(
            old_evidence_id,
            [evidence["evidence_id"] for evidence in refreshed["evidence"]],
        )
        with self.assertRaises(KeyError):
            reader.get_source(evidence_id=old_evidence_id)
        source_readback = reader.get_source(source_id=client_source.source_id)
        evidence_readback = reader.get_source(evidence_id=new_evidence_id)
        self.assertIn(changed_text, source_readback["content"])
        self.assertIn(changed_text, evidence_readback["source_text"])
        self.assertEqual(
            source_readback["index_version"],
            evidence_readback["index_version"],
        )

        self.path.write_text(markdown(), encoding="utf-8")
        restored = writer.sync_source(client_source.source_id)
        restored_result = reader.search(
            "Beta paragraph",
            mode="hybrid",
            top_k=8,
            client_ids=["client-a"],
        )
        self.assertEqual(restored_result["index_version"], restored["index_version"])
        self.assertIn(
            old_evidence_id,
            [evidence["evidence_id"] for evidence in restored_result["evidence"]],
        )
        with self.assertRaises(KeyError):
            reader.get_source(evidence_id=new_evidence_id)

    def test_get_source_rejects_evidence_from_an_uncommitted_document_hash(
        self,
    ) -> None:
        service = KnowledgeService(self.settings, FakeEmbedder())
        service.sync_all()
        row = next(iter(service.index.rows_for_source(self.source.source_id).values()))
        stale_row = {**row, "document_hash": "0" * 64}

        with (
            patch.object(service.index, "get_chunk", return_value=stale_row),
            self.assertRaises(IndexIntegrityError),
        ):
            service.get_source(evidence_id=f"ev_{row['chunk_id']}")

    def test_path_rename_updates_metadata_without_embedding(self) -> None:
        embedder = FakeEmbedder()
        service = KnowledgeService(self.settings, embedder)
        first = service.sync_all()[0]
        renamed = self.vault / "project" / "renamed.md"
        self.path.rename(renamed)
        renamed_source = self.source.model_copy(
            update={"relative_path": "project/renamed.md"}
        )
        self.write_registry(renamed_source)

        service = KnowledgeService(self.settings, embedder)
        second = service.sync_all()[0]
        self.assertEqual(second["embedded_count"], 0)
        self.assertEqual(second["reused_count"], first["chunk_count"])
        rows = service.index.rows_for_source(self.source.source_id)
        self.assertTrue(
            all(row["relative_path"] == "project/renamed.md" for row in rows.values())
        )

    def test_unregistered_file_is_ignored(self) -> None:
        embedder = FakeEmbedder()
        service = KnowledgeService(self.settings, embedder)
        service.sync_all()
        (self.vault / "project" / "unregistered.md").write_text(
            markdown("Do not index me"),
            encoding="utf-8",
        )
        self.assertTrue(service.assert_ready()["ready"])
        self.assertEqual(len(service.state.list_sources()), 1)

    def test_remove_source_purges_registry_state_and_index(self) -> None:
        service = KnowledgeService(self.settings, FakeEmbedder())
        synced = service.sync_all()[0]
        previous_version = synced["index_version"]

        removed = service.remove_source(self.source.source_id)

        self.assertEqual(removed["action"], "removed")
        self.assertEqual(removed["deleted_index_chunks"], synced["chunk_count"])
        self.assertEqual(removed["deleted_state_chunks"], synced["chunk_count"])
        self.assertEqual(removed["index_version"], previous_version + 1)
        self.assertEqual(load_registry(self.registry_path).sources, [])
        self.assertIsNone(service.state.get_source(self.source.source_id))
        self.assertEqual(service.index.rows_for_source(self.source.source_id), {})
        with self.assertRaises(NotIndexedError):
            service.assert_ready()

        restored = KnowledgeService(self.settings, FakeEmbedder())
        self.assertEqual(restored.status()["sources"], [])
        upsert_source(self.registry_path, self.source)
        readded = KnowledgeService(self.settings, FakeEmbedder())
        result = readded.sync_all()[0]
        self.assertEqual(result["status"], "fresh")
        self.assertTrue(readded.status()["ready"])

    def test_embedding_failure_blocks_old_index(self) -> None:
        service = KnowledgeService(self.settings, FakeEmbedder())
        service.sync_all()
        self.path.write_text(markdown("Changed during outage."), encoding="utf-8")
        failing = KnowledgeService(
            self.settings,
            FakeEmbedder(fail=True),
        )
        with self.assertRaises(EmbeddingUnavailableError):
            failing.sync_all()
        status = failing.status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["blocking_sources"][0]["status"], "failed")
        with self.assertRaises(FreshnessError):
            failing.assert_ready()


if __name__ == "__main__":
    unittest.main()
