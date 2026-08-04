"""Explicit source registration and path safety."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .errors import ConfigurationError, IdentityConflictError, SourceMissingError
from .models import SourceRegistry, SourceSpec


def derive_source_id(
    project_id: str,
    document_role: str,
    client_id: str = "",
) -> str:
    identity = "\x1f".join(
        [project_id.strip(), client_id.strip(), document_role.strip()]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def make_source_spec(
    *,
    project_id: str,
    project_name: str,
    document_role: str,
    relative_path: str,
    client_id: str = "",
    enabled: bool = True,
) -> SourceSpec:
    return SourceSpec(
        source_id=derive_source_id(project_id, document_role, client_id),
        project_id=project_id,
        project_name=project_name,
        client_id=client_id,
        document_role=document_role,
        relative_path=relative_path,
        enabled=enabled,
    )


def load_registry(path: Path) -> SourceRegistry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        registry = SourceRegistry.model_validate(payload)
    except Exception as exc:
        raise ConfigurationError(
            f"Invalid source registry: {path}",
            details={"path": str(path), "error": str(exc)},
        ) from exc
    validate_registry_uniqueness(registry)
    return registry


def write_registry(path: Path, registry: SourceRegistry) -> None:
    validate_registry_uniqueness(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            registry.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def upsert_source(path: Path, source: SourceSpec) -> str:
    registry = load_registry(path)
    sources = []
    action = "added"
    for existing in registry.sources:
        if existing.source_id == source.source_id:
            sources.append(source)
            action = "updated"
        else:
            sources.append(existing)
    if action == "added":
        sources.append(source)
    write_registry(path, SourceRegistry(schema_version=1, sources=sources))
    return action


def remove_source(path: Path, source_id: str) -> SourceSpec:
    registry = load_registry(path)
    removed: SourceSpec | None = None
    remaining = []
    for source in registry.sources:
        if source.source_id == source_id:
            removed = source
        else:
            remaining.append(source)
    if removed is None:
        raise KeyError(f"Unknown source_id: {source_id}")
    write_registry(path, SourceRegistry(schema_version=1, sources=remaining))
    return removed


def update_source_path(
    path: Path,
    source_id: str,
    relative_path: str,
) -> SourceSpec:
    registry = load_registry(path)
    updated: SourceSpec | None = None
    sources = []
    for source in registry.sources:
        if source.source_id == source_id:
            source = source.model_copy(update={"relative_path": relative_path})
            updated = source
        sources.append(source)
    if updated is None:
        raise KeyError(f"Unknown source_id: {source_id}")
    write_registry(path, SourceRegistry(schema_version=1, sources=sources))
    return updated


def set_source_enabled(path: Path, source_id: str, enabled: bool) -> SourceSpec:
    registry = load_registry(path)
    updated: SourceSpec | None = None
    sources = []
    for source in registry.sources:
        if source.source_id == source_id:
            source = source.model_copy(update={"enabled": enabled})
            updated = source
        sources.append(source)
    if updated is None:
        raise KeyError(f"Unknown source_id: {source_id}")
    write_registry(path, SourceRegistry(schema_version=1, sources=sources))
    return updated


def validate_registry_uniqueness(registry: SourceRegistry) -> None:
    ids: dict[str, SourceSpec] = {}
    identities: dict[tuple[str, str, str], SourceSpec] = {}
    paths: dict[str, SourceSpec] = {}
    for source in registry.sources:
        identity = (
            source.project_id.casefold(),
            source.client_id.casefold(),
            source.document_role.casefold(),
        )
        normalized_path = source.relative_path.casefold()
        if source.source_id in ids:
            raise IdentityConflictError(f"Duplicate source_id: {source.source_id}")
        if identity in identities:
            raise IdentityConflictError(
                "Duplicate project/client/document_role identity",
                details={
                    "source_id": source.source_id,
                    "conflicts_with": identities[identity].source_id,
                },
            )
        if normalized_path in paths:
            raise IdentityConflictError(
                f"One path is registered more than once: {source.relative_path}"
            )
        expected_id = derive_source_id(
            source.project_id,
            source.document_role,
            source.client_id,
        )
        if source.source_id != expected_id:
            raise IdentityConflictError(
                f"source_id does not match stable identity: {source.source_id}",
                details={"expected": expected_id},
            )
        ids[source.source_id] = source
        identities[identity] = source
        paths[normalized_path] = source


def resolve_source_path(vault_root: Path, source: SourceSpec) -> Path:
    root = vault_root.expanduser().resolve(strict=True)
    candidate = (root / source.relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(
            "Registered source resolves outside the selected Vault",
            details={
                "source_id": source.source_id,
                "relative_path": source.relative_path,
            },
        ) from exc
    if not candidate.is_file():
        raise SourceMissingError(
            f"Registered source is missing: {source.relative_path}",
            details={
                "source_id": source.source_id,
                "path": str(candidate),
            },
        )
    return candidate
