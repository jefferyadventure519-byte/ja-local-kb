from __future__ import annotations

import json

from ja_local_kb.cli import main, parser, toml_quote
from ja_local_kb.models import SourceRegistry
from ja_local_kb.registry import CLIENT_PROJECT_ID_PREFIX, load_registry, write_registry


def test_toml_quote_preserves_chinese_windows_paths() -> None:
    path = "D:\\知识库\\配置.json"
    quoted = toml_quote(path)
    assert json.loads(quoted) == path
    assert "知识库" in quoted
    assert "\\u" not in quoted


def test_search_accepts_repeatable_client_ids() -> None:
    args = parser().parse_args(
        [
            "--settings",
            "settings.json",
            "search",
            "客户规则",
            "--client-id",
            "example-a",
            "--client-id",
            "example-b",
        ]
    )
    assert args.client_ids == ["example-a", "example-b"]


def test_add_client_does_not_require_project_id() -> None:
    args = parser().parse_args(
        [
            "--settings",
            "settings.json",
            "source",
            "add-client",
            "--client-id",
            "example-a",
            "--client-name",
            "EXAMPLEA",
            "--document-role",
            "client_overview",
            "--relative-path",
            "clients/example-a/EXAMPLEA｜00_客户入口.md",
        ]
    )
    assert args.source_command == "add-client"
    assert not hasattr(args, "project_id")


def test_add_client_cli_writes_compatible_source(tmp_path, capsys) -> None:
    vault = tmp_path / "vault"
    runtime = tmp_path / "runtime"
    vault.mkdir()
    registry = tmp_path / "sources.json"
    write_registry(registry, SourceRegistry(schema_version=1, sources=[]))
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "vault_root": str(vault),
                "runtime_root": str(runtime),
                "source_registry": str(registry),
                "embedding": {"provider": "openai", "model": "unused"},
            }
        ),
        encoding="utf-8",
    )
    main(
        [
            "--settings",
            str(settings),
            "source",
            "add-client",
            "--client-id",
            "example-a",
            "--client-name",
            "EXAMPLEA",
            "--document-role",
            "client_overview",
            "--relative-path",
            "clients/example-a/EXAMPLEA｜00_客户入口.md",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    source = load_registry(registry).sources[0]
    assert output["action"] == "added"
    assert source.client_id == "example-a"
    assert source.project_id == f"{CLIENT_PROJECT_ID_PREFIX}example-a"
