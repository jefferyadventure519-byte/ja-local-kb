from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from mcp.client import Client
from test_incremental_service import markdown

from ja_local_kb.mcp_server import build_server
from ja_local_kb.models import SourceRegistry
from ja_local_kb.registry import make_source_spec, write_registry


class McpServerTests(unittest.TestCase):
    def test_protocol_lists_exactly_three_public_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            runtime = root / "runtime"
            (vault / "project").mkdir(parents=True)
            (vault / "project" / "overview.md").write_text(
                markdown(),
                encoding="utf-8",
            )
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
            settings = root / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "vault_root": str(vault),
                        "runtime_root": str(runtime),
                        "source_registry": str(registry),
                        "embedding": {
                            "provider": "openai",
                            "model": "unused",
                        },
                    }
                ),
                encoding="utf-8",
            )
            server = build_server(settings, "environment")

            async def exercise() -> None:
                async with Client(server) as client:
                    tools = await client.list_tools()
                    self.assertEqual(
                        {tool.name for tool in tools.tools},
                        {
                            "search_knowledge",
                            "get_source",
                            "get_knowledge_status",
                        },
                    )
                    search_tool = next(
                        tool for tool in tools.tools if tool.name == "search_knowledge"
                    )
                    self.assertEqual(
                        search_tool.input_schema["properties"]["mode"]["default"],
                        "recall",
                    )
                    result = await client.call_tool(
                        "get_knowledge_status",
                        {},
                    )
                    self.assertFalse(result.is_error)
                    self.assertIsNotNone(result.structured_content)
                    self.assertTrue(result.structured_content["ok"])
                    self.assertFalse(result.structured_content["result"]["ready"])

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
