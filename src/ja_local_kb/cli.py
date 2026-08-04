"""Internal operational CLI; MCP remains the public Agent contract."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import platform
import sys
from pathlib import Path

from . import __version__
from .benchmark import (
    load_questions,
    run_benchmark,
    validate_corpus,
    write_result,
)
from .config import load_settings
from .connection_guide import build_connection_guide
from .embedding import ApiEmbedder
from .errors import KnowledgeBaseError
from .quality_report import build_quality_report
from .rebuild import rebuild_index
from .registry import (
    load_registry,
    make_source_spec,
    set_source_enabled,
    update_source_path,
    upsert_source,
)
from .runtime import ServiceManager
from .watcher import WatchDaemon


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ja-kb")
    root.add_argument(
        "--settings",
        type=Path,
        default=os.environ.get("JA_KB_SETTINGS"),
        required=not bool(os.environ.get("JA_KB_SETTINGS")),
    )
    root.add_argument(
        "--secret-resolver",
        choices=("keyring", "environment"),
        default=os.environ.get("JA_KB_SECRET_RESOLVER", "keyring"),
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    sync = commands.add_parser("sync")
    sync.add_argument("--source-id")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument(
        "--mode",
        choices=("keyword", "vector", "hybrid", "smart", "recall", "quality"),
        default="recall",
    )
    search.add_argument("--top-k", type=int)
    search.add_argument("--project-id", action="append", dest="project_ids")
    commands.add_parser("watch")
    rebuild = commands.add_parser("rebuild")
    rebuild.add_argument("--confirm", action="store_true")
    commands.add_parser("doctor")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--questions", type=Path, required=True)
    benchmark.add_argument("--corrections", type=Path)
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument(
        "--mode",
        choices=("keyword", "vector", "hybrid", "smart", "recall", "quality"),
        default="recall",
    )
    benchmark.add_argument("--top-k", type=int)
    benchmark.add_argument("--repeats", type=int, default=3)
    benchmark.add_argument("--validate-only", action="store_true")
    connection = commands.add_parser("connection-config")
    connection.add_argument("--client", choices=("codex", "claude"), required=True)
    guide = commands.add_parser("connection-guide")
    guide.add_argument(
        "--client",
        choices=("codex", "claude", "other"),
        required=True,
    )
    quality_report = commands.add_parser("quality-report")
    quality_report.add_argument("--report", type=Path)
    source = commands.add_parser("source")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_commands.add_parser("list")
    add = source_commands.add_parser("add")
    add.add_argument("--project-id", required=True)
    add.add_argument("--project-name", required=True)
    add.add_argument("--document-role", required=True)
    add.add_argument("--relative-path", required=True)
    add.add_argument("--client-id", default="")
    remove = source_commands.add_parser("remove")
    remove.add_argument("--source-id", required=True)
    move = source_commands.add_parser("move")
    move.add_argument("--source-id", required=True)
    move.add_argument("--relative-path", required=True)
    enable = source_commands.add_parser("enable")
    enable.add_argument("--source-id", required=True)
    disable = source_commands.add_parser("disable")
    disable.add_argument("--source-id", required=True)
    secret = commands.add_parser("set-secret")
    secret.add_argument("--name")
    return root


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        manager = ServiceManager(
            args.settings,
            secret_resolver=args.secret_resolver,
        )
        if args.command == "status":
            print_json(manager.get().status())
        elif args.command == "sync":
            result = (
                manager.get().sync_source(args.source_id)
                if args.source_id
                else manager.get().sync_all()
            )
            print_json(result)
        elif args.command == "search":
            print_json(
                manager.get().search(
                    args.query,
                    mode=args.mode,
                    top_k=args.top_k,
                    project_ids=args.project_ids,
                )
            )
        elif args.command == "watch":
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
            )
            WatchDaemon(manager).run()
        elif args.command == "rebuild":
            if not args.confirm:
                raise ValueError("Full rebuild requires --confirm after backup review")
            settings = load_settings(args.settings)
            print_json(
                rebuild_index(
                    settings,
                    ApiEmbedder(settings.embedding, manager.resolver),
                )
            )
        elif args.command == "doctor":
            service = manager.get()
            checks = []
            try:
                manager.resolver.get(service.settings.embedding.secret_name)
                checks.append(
                    {
                        "name": "embedding_credential",
                        "ok": True,
                        "message": "available in configured resolver",
                    }
                )
            except Exception as exc:
                checks.append(
                    {
                        "name": "embedding_credential",
                        "ok": False,
                        "message": str(exc),
                    }
                )
            if service.settings.reranker is not None:
                reranker_secret = service.settings.reranker.secret_name
                if reranker_secret == service.settings.embedding.secret_name:
                    checks.append(
                        {
                            "name": "reranker_credential",
                            "ok": checks[-1]["ok"],
                            "message": "shares the embedding credential",
                        }
                    )
                else:
                    try:
                        manager.resolver.get(reranker_secret)
                        checks.append(
                            {
                                "name": "reranker_credential",
                                "ok": True,
                                "message": "available in configured resolver",
                            }
                        )
                    except Exception as exc:
                        checks.append(
                            {
                                "name": "reranker_credential",
                                "ok": False,
                                "message": str(exc),
                            }
                        )
            status = service.status()
            checks.extend(
                [
                    {
                        "name": "vault_root",
                        "ok": service.settings.vault_root.is_dir(),
                        "message": str(service.settings.vault_root),
                    },
                    {
                        "name": "registered_sources",
                        "ok": len(service.registry.sources) > 0,
                        "message": str(len(service.registry.sources)),
                    },
                    {
                        "name": "retrieval_ready",
                        "ok": status["ready"],
                        "message": (
                            "fresh"
                            if status["ready"]
                            else f"{len(status['blocking_sources'])} blocking"
                        ),
                    },
                ]
            )
            print_json(
                {
                    "healthy": all(check["ok"] for check in checks),
                    "version": __version__,
                    "platform": platform.platform(),
                    "python": sys.version.split()[0],
                    "checks": checks,
                    "status": {
                        "ready": status["ready"],
                        "index_version": status["index_version"],
                        "counts": status["counts"],
                        "blocking_sources": status["blocking_sources"],
                    },
                }
            )
        elif args.command == "benchmark":
            settings = load_settings(args.settings)
            question_bytes, question_payload = load_questions(
                args.questions,
                args.corrections,
            )
            validation = validate_corpus(settings, question_payload)
            if not validation["ground_truth_valid"]:
                raise ValueError(
                    "Benchmark ground truth is absent from the current chunks: "
                    + json.dumps(
                        validation["missing_evidence_groups"],
                        ensure_ascii=False,
                    )
                )
            if args.validate_only:
                print_json(validation)
            else:
                service = manager.get()
                result = run_benchmark(
                    service,
                    question_bytes,
                    question_payload,
                    mode=args.mode,
                    top_k=args.top_k,
                    repeats=args.repeats,
                )
                output = args.output or (
                    settings.runtime_root
                    / "benchmarks"
                    / f"{settings.embedding.model}.json"
                )
                write_result(output, result)
                print_json(
                    {
                        "output": str(output),
                        "summary": result["summary"],
                    }
                )
        elif args.command == "connection-config":
            python_path = str(Path(sys.executable).resolve())
            settings_path = str(Path(args.settings).resolve())
            command_args = [
                "-m",
                "ja_local_kb.mcp_server",
                "--settings",
                settings_path,
            ]
            if args.client == "claude":
                content = json.dumps(
                    {
                        "mcpServers": {
                            "ja_local_knowledge": {
                                "command": python_path,
                                "args": command_args,
                            }
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                lines = [
                    "[mcp_servers.ja_local_knowledge]",
                    f"command = {toml_quote(python_path)}",
                    "args = ["
                    + ", ".join(toml_quote(value) for value in command_args)
                    + "]",
                ]
                content = "\n".join(lines)
            print_json(
                {
                    "client": args.client,
                    "format": "json" if args.client == "claude" else "toml",
                    "content": content,
                }
            )
        elif args.command == "connection-guide":
            print_json(
                build_connection_guide(
                    client=args.client,
                    python_path=Path(sys.executable),
                    settings_path=Path(args.settings),
                )
            )
        elif args.command == "quality-report":
            print_json(
                build_quality_report(
                    manager.get(),
                    report_path=args.report,
                )
            )
        elif args.command == "source":
            settings = load_settings(args.settings)
            if args.source_command == "list":
                print_json(
                    load_registry(settings.source_registry).model_dump(mode="json")
                )
            elif args.source_command == "add":
                source = make_source_spec(
                    project_id=args.project_id,
                    project_name=args.project_name,
                    document_role=args.document_role,
                    relative_path=args.relative_path,
                    client_id=args.client_id,
                )
                action = upsert_source(settings.source_registry, source)
                print_json(
                    {
                        "action": action,
                        "source": source.model_dump(mode="json"),
                    }
                )
            elif args.source_command == "remove":
                print_json(manager.get().remove_source(args.source_id))
            elif args.source_command == "move":
                updated = update_source_path(
                    settings.source_registry,
                    args.source_id,
                    args.relative_path,
                )
                print_json(
                    {
                        "action": "moved",
                        "source": updated.model_dump(mode="json"),
                    }
                )
            elif args.source_command in {"enable", "disable"}:
                updated = set_source_enabled(
                    settings.source_registry,
                    args.source_id,
                    args.source_command == "enable",
                )
                print_json(
                    {
                        "action": args.source_command,
                        "source": updated.model_dump(mode="json"),
                    }
                )
        elif args.command == "set-secret":
            settings = manager.get().settings
            secret_name = args.name or settings.embedding.secret_name
            if args.secret_resolver != "keyring":
                raise ValueError("set-secret requires the keyring resolver")
            import keyring

            value = getpass.getpass(f"API key for {secret_name}: ").strip()
            if not value:
                raise ValueError("Secret cannot be empty")
            keyring.set_password("ja-local-kb", secret_name, value)
            print_json({"stored": True, "secret_name": secret_name})
    except KnowledgeBaseError as exc:
        print_json(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
            }
        )
        raise SystemExit(2) from None
    except Exception as exc:
        print_json(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": str(exc),
                },
            }
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main(sys.argv[1:])
