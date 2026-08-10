"""Local STDIO MCP server. It returns evidence and never writes Vault content."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from . import __version__
from .facade import ToolFacade
from .runtime import ServiceManager


def build_server(settings_path: Path, secret_resolver: str = "keyring"):
    from mcp.server.mcpserver import MCPServer

    facade = ToolFacade(ServiceManager(settings_path, secret_resolver=secret_resolver))
    server = MCPServer(
        "JA Local Knowledge",
        version=__version__,
        instructions=(
            "Retrieve traceable evidence from the device owner's explicitly "
            "selected Obsidian sources. Do not treat evidence as a final answer."
        ),
    )

    @server.tool(structured_output=True)
    def search_knowledge(
        query: str,
        mode: str = "recall",
        top_k: int | None = None,
        project_ids: list[str] | None = None,
        include_candidates: bool = False,
        client_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a complete high-recall evidence pool by default."""
        return facade.search_knowledge(
            query,
            mode,
            top_k,
            project_ids,
            include_candidates,
            client_ids,
        )

    @server.tool(structured_output=True)
    def get_source(
        source_id: str | None = None,
        evidence_id: str | None = None,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        """Read one registered source or one returned evidence chunk."""
        return facade.get_source(source_id, evidence_id, max_chars)

    @server.tool(structured_output=True)
    def get_knowledge_status() -> dict[str, Any]:
        """Return freshness, source coverage, vector space, and blocking errors."""
        return facade.get_knowledge_status()

    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--settings",
        type=Path,
        default=os.environ.get("JA_KB_SETTINGS"),
        required=not bool(os.environ.get("JA_KB_SETTINGS")),
    )
    parser.add_argument(
        "--secret-resolver",
        choices=("keyring", "environment"),
        default=os.environ.get("JA_KB_SECRET_RESOLVER", "keyring"),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    build_server(args.settings, args.secret_resolver).run(transport="stdio")


if __name__ == "__main__":
    main()
