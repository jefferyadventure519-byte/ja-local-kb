from __future__ import annotations

import json

from ja_local_kb.cli import toml_quote


def test_toml_quote_preserves_chinese_windows_paths() -> None:
    path = "D:\\知识库\\配置.json"
    quoted = toml_quote(path)
    assert json.loads(quoted) == path
    assert "知识库" in quoted
    assert "\\u" not in quoted
