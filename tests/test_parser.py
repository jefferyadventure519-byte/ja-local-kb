from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ja_local_kb.errors import IdentityConflictError
from ja_local_kb.models import ChunkingConfig
from ja_local_kb.parser import parse_source
from ja_local_kb.registry import make_source_spec

MARKDOWN = """---
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

## Current decision

Use the local MCP. Link to [[02_Decisions#D001]].

## Details

### First

Alpha paragraph.

### Second

Beta paragraph.
"""


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = make_source_spec(
            project_id="project-a",
            project_name="Project A",
            document_role="project_overview",
            relative_path="project/overview.md",
        )

    def test_structure_and_metadata_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overview.md"
            path.write_text(MARKDOWN, encoding="utf-8")
            chunks = parse_source(path, self.source, ChunkingConfig(max_chars=300))
            self.assertGreaterEqual(len(chunks), 3)
            self.assertTrue(
                all(chunk.source_id == self.source.source_id for chunk in chunks)
            )
            self.assertTrue(
                any("Current decision" in chunk.heading for chunk in chunks)
            )
            self.assertTrue(
                any("02_Decisions#D001" in chunk.wikilinks_json for chunk in chunks)
            )
            self.assertTrue(all(chunk.embedding_hash for chunk in chunks))

    def test_path_rename_does_not_change_chunk_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.md"
            second = root / "renamed.md"
            first.write_text(MARKDOWN, encoding="utf-8")
            second.write_text(MARKDOWN, encoding="utf-8")
            first_chunks = parse_source(
                first,
                self.source,
                ChunkingConfig(max_chars=300),
            )
            renamed_source = self.source.model_copy(
                update={"relative_path": "project/renamed.md"}
            )
            second_chunks = parse_source(
                second,
                renamed_source,
                ChunkingConfig(max_chars=300),
            )
            self.assertEqual(
                [chunk.chunk_id for chunk in first_chunks],
                [chunk.chunk_id for chunk in second_chunks],
            )
            self.assertEqual(
                [chunk.embedding_hash for chunk in first_chunks],
                [chunk.embedding_hash for chunk in second_chunks],
            )

    def test_frontmatter_identity_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overview.md"
            path.write_text(
                MARKDOWN.replace("project_id: project-a", "project_id: project-b"),
                encoding="utf-8",
            )
            with self.assertRaises(IdentityConflictError):
                parse_source(path, self.source, ChunkingConfig())

    def test_overlap_cannot_make_hard_split_loop_forever(self) -> None:
        with self.assertRaises(ValueError):
            ChunkingConfig(max_chars=300, overlap_chars=300)


if __name__ == "__main__":
    unittest.main()
