from __future__ import annotations

from pathlib import Path

import pytest

from ja_local_kb.connection_guide import build_connection_guide


@pytest.mark.parametrize("client", ["codex", "claude", "other"])
def test_connection_guide_is_review_first_and_verifiable(client: str) -> None:
    python_path = Path("D:/知识库/runtime/python.exe")
    settings_path = Path("D:/知识库/config/settings.json")
    result = build_connection_guide(
        client=client,
        python_path=python_path,
        settings_path=settings_path,
    )
    prompt = result["prompt"]
    assert "现在只执行第 1 步" in prompt
    assert "等我明确回复确认" in prompt
    assert "禁止从 main" in prompt
    assert "保留哪些现有 MCP" in prompt
    assert "API Key" in prompt
    assert "不要向我索取" in prompt
    assert "get_knowledge_status" in prompt
    assert "search_knowledge" in prompt
    assert "get_source" in prompt
    assert "只看到配置文件不能宣称“已连接”" in prompt
    assert str(python_path.resolve()) in prompt
    assert str(settings_path.resolve()) in prompt
    assert "知识库" in prompt


def test_connection_guide_rejects_unknown_client() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        build_connection_guide(
            client="unknown",
            python_path=Path("python"),
            settings_path=Path("settings.json"),
        )
