"""Staged full rebuild with recoverable local index swap."""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .embedding import Embedder
from .errors import NotIndexedError
from .locking import RuntimeLock
from .models import AppSettings
from .service import KnowledgeService
from .state import StateStore


def backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(destination)) as destination_connection,
    ):
        source_connection.backup(destination_connection)


def rebuild_index(settings: AppSettings, embedder: Embedder) -> dict:
    staging_root = settings.runtime_root / "rebuild" / uuid.uuid4().hex
    staging_settings = settings.model_copy(
        update={
            "runtime_root": staging_root / "runtime",
            "state_path": staging_root / "state.sqlite3",
            "lancedb_root": staging_root / "lancedb",
        }
    )
    staging_service = KnowledgeService(staging_settings, embedder)
    previous_version = (
        StateStore(settings.resolved_state_path).index_version()
        if settings.resolved_state_path.exists()
        else 0
    )
    staging_service.state.set_index_version(previous_version)
    enabled = [source for source in staging_service.registry.sources if source.enabled]
    if not enabled:
        raise NotIndexedError("No enabled sources are available for rebuild")
    sync_results = staging_service.sync_all()
    staging_service.assert_ready()

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    backup_root = settings.runtime_root / "rebuild_backups" / stamp
    state_target = settings.resolved_state_path
    lancedb_target = settings.resolved_lancedb_root
    state_backup = backup_root / "state.sqlite3"
    lancedb_backup = backup_root / "lancedb"
    moved_lancedb = False

    with RuntimeLock(settings.runtime_root, timeout=120).exclusive():
        staging_service.assert_ready()
        backup_root.mkdir(parents=True, exist_ok=False)
        if state_target.exists():
            backup_sqlite(state_target, state_backup)
        if lancedb_target.exists():
            lancedb_backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(lancedb_target, lancedb_backup)
            moved_lancedb = True
        try:
            state_target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_settings.resolved_state_path, state_target)
            lancedb_target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_settings.resolved_lancedb_root, lancedb_target)
        except Exception:
            if moved_lancedb and not lancedb_target.exists():
                os.replace(lancedb_backup, lancedb_target)
            if state_backup.exists():
                shutil.copy2(state_backup, state_target)
            raise
    shutil.rmtree(staging_root, ignore_errors=True)
    return {
        "rebuilt": True,
        "source_count": len(sync_results),
        "chunk_count": sum(result["chunk_count"] for result in sync_results),
        "embedded_count": sum(result["embedded_count"] for result in sync_results),
        "index_version": previous_version + len(sync_results),
        "backup_root": str(backup_root),
    }
