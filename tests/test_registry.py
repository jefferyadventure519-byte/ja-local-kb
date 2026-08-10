from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ja_local_kb.errors import IdentityConflictError, SourceMissingError
from ja_local_kb.models import SourceRegistry
from ja_local_kb.registry import (
    CLIENT_PROJECT_ID_PREFIX,
    derive_source_id,
    load_registry,
    make_client_source_spec,
    make_source_spec,
    remove_source,
    resolve_source_path,
    set_source_enabled,
    update_source_path,
    upsert_source,
    validate_registry_uniqueness,
    write_registry,
)


class RegistryTests(unittest.TestCase):
    def test_existing_project_source_id_is_unchanged(self) -> None:
        self.assertEqual(
            derive_source_id("project-a", "project_overview"),
            "c486bcea6152034e4c0f064be1191fb9",
        )

    def test_source_id_ignores_path(self) -> None:
        first = derive_source_id("project-a", "project_overview")
        second = derive_source_id("project-a", "project_overview")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_duplicate_stable_identity_is_rejected(self) -> None:
        source = make_source_spec(
            project_id="project-a",
            project_name="Project A",
            document_role="project_overview",
            relative_path="a/00.md",
        )
        duplicate = source.model_copy(update={"relative_path": "b/renamed.md"})
        registry = SourceRegistry(sources=[source, duplicate])
        with self.assertRaises(IdentityConflictError):
            validate_registry_uniqueness(registry)

    def test_path_escape_is_rejected_by_model(self) -> None:
        with self.assertRaises(ValueError):
            make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="../outside.md",
            )

    def test_resolve_requires_registered_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="missing.md",
            )
            with self.assertRaises(SourceMissingError):
                resolve_source_path(root, source)

    def test_resolve_inside_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "project" / "overview.md"
            path.parent.mkdir()
            path.write_text("# Test\n", encoding="utf-8")
            source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="project/overview.md",
            )
            self.assertEqual(resolve_source_path(root, source), path.resolve())

    def test_registry_mutations_preserve_stable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="overview",
                relative_path="project/overview.md",
            )
            write_registry(
                path,
                SourceRegistry(schema_version=1, sources=[]),
            )
            self.assertEqual(upsert_source(path, source), "added")
            renamed = source.model_copy(update={"relative_path": "project/renamed.md"})
            updated = update_source_path(
                path,
                source.source_id,
                renamed.relative_path,
            )
            self.assertEqual(updated.source_id, source.source_id)
            loaded = load_registry(path).sources[0]
            self.assertEqual(loaded.source_id, source.source_id)
            self.assertEqual(loaded.relative_path, "project/renamed.md")
            self.assertFalse(set_source_enabled(path, source.source_id, False).enabled)
            self.assertEqual(
                remove_source(path, source.source_id).source_id,
                source.source_id,
            )
            self.assertEqual(load_registry(path).sources, [])

    def test_client_source_uses_internal_project_compatibility_fields(self) -> None:
        source = make_client_source_spec(
            client_id="example-a",
            client_name="EXAMPLEA",
            document_role="client_overview",
            relative_path="clients/example-a/EXAMPLEA｜00_客户入口.md",
        )
        self.assertEqual(source.client_id, "example-a")
        self.assertEqual(source.project_name, "EXAMPLEA")
        self.assertEqual(
            source.project_id,
            f"{CLIENT_PROJECT_ID_PREFIX}example-a",
        )

    def test_legacy_registry_without_client_fields_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="project/overview.md",
            )
            payload = source.model_dump(mode="json")
            payload.pop("client_id")
            path.write_text(
                '{"schema_version":1,"sources":['
                + json.dumps(payload)
                + "]}",
                encoding="utf-8",
            )
            loaded = load_registry(path)
            self.assertEqual(loaded.sources[0].client_id, "")
            self.assertEqual(loaded.sources[0].source_id, source.source_id)


if __name__ == "__main__":
    unittest.main()
