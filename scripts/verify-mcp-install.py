from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify an installed JA Local KB STDIO MCP endpoint."
    )
    parser.add_argument("--settings", type=Path, required=True)
    return parser.parse_args()


async def verify(settings: Path) -> dict:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "ja_local_kb.mcp_server",
            "--settings",
            str(settings),
        ],
    )
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        tools = sorted(tool.name for tool in listed.tools)
        expected = [
            "get_knowledge_status",
            "get_source",
            "search_knowledge",
        ]
        if tools != expected:
            raise RuntimeError(f"Unexpected MCP tools: {tools}")
        status_call = await session.call_tool("get_knowledge_status", {})
        status = status_call.structured_content
        if status is None or status.get("ok") is not True:
            raise RuntimeError(f"MCP status failed: {status}")
        return {
            "ok": True,
            "tools": tools,
            "status_readable": True,
            "ready": status.get("result", {}).get("ready"),
        }


def main() -> None:
    args = parse_args()
    print(json.dumps(asyncio.run(verify(args.settings)), indent=2))


if __name__ == "__main__":
    main()
