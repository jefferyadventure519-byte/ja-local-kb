"""Protect device-owned source registration during update and rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

SOURCE_REGISTRY_NAME = "sources.json"


def canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON file: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def registry_state(config_root: Path) -> dict[str, Any]:
    registry = canonical(config_root / SOURCE_REGISTRY_NAME)
    if not registry.is_file():
        raise ValueError(f"Device source registry is missing: {registry}")
    payload = read_json(registry)
    if payload.get("schema_version") != 1 or not isinstance(
        payload.get("sources"), list
    ):
        raise ValueError(f"Invalid device source registry schema: {registry}")
    return {
        "path": str(registry),
        "sha256": hashlib.sha256(registry.read_bytes()).hexdigest().upper(),
        "source_count": len(payload["sources"]),
    }


def settings_registry(settings_path: Path) -> Path:
    settings = read_json(settings_path)
    raw_registry = settings.get("source_registry")
    if not isinstance(raw_registry, str) or not raw_registry.strip():
        raise ValueError(f"settings.source_registry is missing: {settings_path}")
    candidate = Path(raw_registry).expanduser()
    if not candidate.is_absolute():
        candidate = settings_path.parent / candidate
    return canonical(candidate)


def validate_active_settings(config_root: Path) -> dict[str, Any]:
    expected = canonical(config_root / SOURCE_REGISTRY_NAME)
    settings_path = canonical(config_root / "settings.json")
    if not settings_path.is_file():
        raise ValueError(f"Active settings are missing: {settings_path}")
    actual = settings_registry(settings_path)
    if actual != expected:
        raise ValueError(
            "Active settings point to a different source registry: "
            f"expected={expected}, actual={actual}"
        )
    return registry_state(config_root)


def validate_snapshot(config_root: Path, snapshot_config: Path) -> None:
    target_settings = canonical(snapshot_config / "settings.json")
    if not target_settings.is_file():
        return
    expected = canonical(config_root / SOURCE_REGISTRY_NAME)
    actual = settings_registry(target_settings)
    if actual != expected:
        raise ValueError(
            "Target settings point to a different source registry: "
            f"expected={expected}, actual={actual}"
        )


def copy_entry(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.pending")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def snapshot(config_root: Path, snapshot_config: Path) -> dict[str, Any]:
    state = validate_active_settings(config_root)
    if snapshot_config.exists():
        raise ValueError(f"Version config snapshot already exists: {snapshot_config}")
    snapshot_config.mkdir(parents=True)
    copied: list[str] = []
    for item in sorted(config_root.iterdir(), key=lambda path: path.name.lower()):
        if item.name.lower() == SOURCE_REGISTRY_NAME:
            continue
        copy_entry(item, snapshot_config / item.name)
        copied.append(item.name)
    return {
        "operation": "snapshot",
        "snapshot_config": str(canonical(snapshot_config)),
        "copied": copied,
        "excluded": [SOURCE_REGISTRY_NAME],
        "registry": state,
    }


def validate(config_root: Path, snapshot_config: Path) -> dict[str, Any]:
    state = validate_active_settings(config_root)
    validate_snapshot(config_root, snapshot_config)
    return {
        "operation": "validate",
        "snapshot_config": str(canonical(snapshot_config)),
        "registry": state,
    }


def restore(
    config_root: Path,
    snapshot_config: Path,
    expected_source_hash: str,
) -> dict[str, Any]:
    before = validate_active_settings(config_root)
    if before["sha256"] != expected_source_hash.upper():
        raise ValueError(
            "Device source registry changed before restore: "
            f"expected={expected_source_hash.upper()}, actual={before['sha256']}"
        )
    validate_snapshot(config_root, snapshot_config)
    copied: list[str] = []
    ignored: list[str] = []
    for item in sorted(snapshot_config.iterdir(), key=lambda path: path.name.lower()):
        if item.name.lower() == SOURCE_REGISTRY_NAME:
            ignored.append(item.name)
            continue
        copy_entry(item, config_root / item.name)
        copied.append(item.name)
    after = validate_active_settings(config_root)
    if after["sha256"] != before["sha256"]:
        raise ValueError(
            "Device source registry changed during restore: "
            f"before={before['sha256']}, after={after['sha256']}"
        )
    return {
        "operation": "restore",
        "snapshot_config": str(canonical(snapshot_config)),
        "copied": copied,
        "ignored": ignored,
        "registry": after,
    }


def inspect(config_root: Path, expected_source_hash: str | None) -> dict[str, Any]:
    state = validate_active_settings(config_root)
    if expected_source_hash and state["sha256"] != expected_source_hash.upper():
        raise ValueError(
            "Device source registry hash mismatch: "
            f"expected={expected_source_hash.upper()}, actual={state['sha256']}"
        )
    return {"operation": "inspect", "registry": state}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "validate", "restore"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--config-root", type=Path, required=True)
        command_parser.add_argument("--snapshot-config", type=Path, required=True)
        if command == "restore":
            command_parser.add_argument("--expected-source-hash", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--config-root", type=Path, required=True)
    inspect_parser.add_argument("--expected-source-hash")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "snapshot":
            result = snapshot(args.config_root, args.snapshot_config)
        elif args.command == "validate":
            result = validate(args.config_root, args.snapshot_config)
        elif args.command == "restore":
            result = restore(
                args.config_root,
                args.snapshot_config,
                args.expected_source_hash,
            )
        else:
            result = inspect(args.config_root, args.expected_source_hash)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
