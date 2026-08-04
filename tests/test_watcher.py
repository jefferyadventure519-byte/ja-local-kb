from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_incremental_service import FakeEmbedder, markdown

from ja_local_kb.models import (
    AppSettings,
    ChunkingConfig,
    EmbeddingConfig,
    SourceRegistry,
)
from ja_local_kb.registry import load_registry, make_source_spec, write_registry
from ja_local_kb.runtime import ServiceManager
from ja_local_kb.service import KnowledgeService
from ja_local_kb.watcher import WatchDaemon


class RecordingService:
    def __init__(
        self,
        vault: Path,
        registry_path: Path,
        registry: SourceRegistry,
    ) -> None:
        self.settings = SimpleNamespace(
            vault_root=vault,
            source_registry=registry_path,
        )
        self.registry = registry
        self.detected = threading.Event()
        self.synced = threading.Event()
        self.synced_source_ids: list[str] = []
        self.drift: list[dict] = []

    def detect_drift(self) -> list[dict]:
        self.detected.set()
        return self.drift

    def sync_source(self, source_id: str) -> dict:
        self.synced_source_ids.append(source_id)
        self.synced.set()
        return {"source_id": source_id, "status": "fresh"}


class RecordingManager:
    def __init__(self, service: RecordingService) -> None:
        self.service = service

    def get(self) -> RecordingService:
        return self.service


def recording_fixture(root: Path) -> tuple[Path, object, RecordingService]:
    vault = root / "vault"
    (vault / "project").mkdir(parents=True)
    source_path = vault / "project" / "overview.md"
    source_path.write_text(markdown(), encoding="utf-8")
    source = make_source_spec(
        project_id="project-a",
        project_name="Project A",
        document_role="project_overview",
        relative_path="project/overview.md",
    )
    registry = SourceRegistry(schema_version=1, sources=[source])
    registry_path = root / "sources.json"
    write_registry(registry_path, registry)
    service = RecordingService(vault, registry_path, registry)
    return source_path, source, service


class WatcherTests(unittest.TestCase):
    def test_registry_change_schedules_only_detected_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, source, service = recording_fixture(Path(directory))
            service.drift = [
                {"source_id": source.source_id, "status": "not_indexed"}
            ]
            daemon = WatchDaemon(RecordingManager(service))
            with patch.object(daemon, "schedule") as schedule:
                daemon.handle_path(service.settings.source_registry)
            schedule.assert_called_once_with(source.source_id)

    def test_atomic_registry_replace_schedules_detected_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, source, service = recording_fixture(root)
            service.drift = [
                {"source_id": source.source_id, "status": "not_indexed"}
            ]
            daemon = WatchDaemon(RecordingManager(service))
            temporary = root / ".sources.json.pending"
            with patch.object(daemon, "schedule") as schedule:
                daemon.handle_move(temporary, service.settings.source_registry)
            schedule.assert_called_once_with(source.source_id)

    def test_background_worker_serializes_source_syncs(self) -> None:
        class ConcurrencyService(RecordingService):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.lock = threading.Lock()
                self.active = 0
                self.max_active = 0
                self.completed = 0
                self.all_done = threading.Event()

            def sync_source(self, source_id: str) -> dict:
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.05)
                with self.lock:
                    self.active -= 1
                    self.completed += 1
                    if self.completed == 2:
                        self.all_done.set()
                return {"source_id": source_id, "status": "fresh"}

        with tempfile.TemporaryDirectory() as directory:
            vault_path, source, recorded = recording_fixture(Path(directory))
            service = ConcurrencyService(
                recorded.settings.vault_root,
                recorded.settings.source_registry,
                recorded.registry,
            )
            daemon = WatchDaemon(RecordingManager(service))
            daemon._start_worker()
            daemon._enqueue(source.source_id)
            daemon._enqueue(f"{source.source_id}-second")
            self.assertTrue(service.all_done.wait(timeout=5))
            daemon._stop_worker()
            self.assertEqual(service.max_active, 1)
            self.assertTrue(vault_path.is_file())

    def test_run_scans_immediately_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, _, service = recording_fixture(Path(directory))
            daemon = WatchDaemon(
                RecordingManager(service),
                scan_interval_seconds=60,
            )
            errors: list[BaseException] = []

            def run() -> None:
                try:
                    daemon.run()
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(service.detected.wait(timeout=5))
            daemon.stop()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_registered_file_change_triggers_debounced_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path, source, service = recording_fixture(Path(directory))
            daemon = WatchDaemon(
                RecordingManager(service),
                debounce_seconds=0.05,
                scan_interval_seconds=60,
            )
            errors: list[BaseException] = []

            def run() -> None:
                try:
                    daemon.run()
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(service.detected.wait(timeout=5))
            source_path.write_text(markdown() + "\nchanged\n", encoding="utf-8")
            self.assertTrue(service.synced.wait(timeout=5))
            daemon.stop()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertIn(source.source_id, service.synced_source_ids)

    def test_file_event_completes_incremental_chunk_and_vector_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, source, recorded = recording_fixture(root)
            settings = AppSettings(
                vault_root=recorded.settings.vault_root,
                runtime_root=root / "runtime",
                source_registry=recorded.settings.source_registry,
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="unused-in-test",
                ),
                chunking=ChunkingConfig(max_chars=500),
            )
            service = KnowledgeService(settings, FakeEmbedder())
            first = service.sync_source(source.source_id)
            sync_finished = threading.Event()
            results: list[dict] = []
            original_sync = service.sync_source

            def sync_source(source_id: str) -> dict:
                result = original_sync(source_id)
                results.append(result)
                sync_finished.set()
                return result

            service.sync_source = sync_source  # type: ignore[method-assign]
            daemon = WatchDaemon(
                RecordingManager(service),  # type: ignore[arg-type]
                debounce_seconds=0.05,
                scan_interval_seconds=60,
            )
            errors: list[BaseException] = []

            def run() -> None:
                try:
                    daemon.run()
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run)
            thread.start()
            source_path.write_text(
                markdown("Changed through watcher."),
                encoding="utf-8",
            )
            self.assertTrue(sync_finished.wait(timeout=10))
            daemon.stop()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results[0]["embedded_count"], 1)
            self.assertEqual(
                results[0]["reused_count"],
                first["chunk_count"] - 1,
            )
            self.assertTrue(service.status()["ready"])
            rows = service.index.rows_for_source(source.source_id)
            self.assertTrue(
                any(
                    "Changed through watcher." in row["source_text"]
                    for row in rows.values()
                )
            )

    def test_unexpected_observer_exit_is_reported_to_supervisor(self) -> None:
        class UnexpectedStopObserver:
            def __init__(self) -> None:
                self.alive = False

            def schedule(self, *args, **kwargs) -> None:
                pass

            def start(self) -> None:
                self.alive = True

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout=None) -> None:
                self.alive = False

            def stop(self) -> None:
                self.alive = False

        with tempfile.TemporaryDirectory() as directory:
            _, _, service = recording_fixture(Path(directory))
            daemon = WatchDaemon(
                RecordingManager(service),
                scan_interval_seconds=0.01,
            )
            with (
                patch("watchdog.observers.Observer", UnexpectedStopObserver),
                self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"),
            ):
                daemon.run()

    def test_registered_move_updates_path_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            runtime = root / "runtime"
            (vault / "project").mkdir(parents=True)
            original = vault / "project" / "overview.md"
            renamed = vault / "project" / "renamed.md"
            original.write_text(markdown(), encoding="utf-8")
            source = make_source_spec(
                project_id="project-a",
                project_name="Project A",
                document_role="project_overview",
                relative_path="project/overview.md",
            )
            registry_path = root / "sources.json"
            write_registry(
                registry_path,
                SourceRegistry(schema_version=1, sources=[source]),
            )
            settings_path = root / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "vault_root": str(vault),
                        "runtime_root": str(runtime),
                        "source_registry": str(registry_path),
                        "embedding": {
                            "provider": "openai",
                            "model": "unused",
                        },
                    }
                ),
                encoding="utf-8",
            )
            manager = ServiceManager(
                settings_path,
                secret_resolver="environment",
            )
            daemon = WatchDaemon(manager)
            original.rename(renamed)
            self.assertTrue(daemon._move_registered_path(source.source_id, renamed))
            updated = load_registry(registry_path).sources[0]
            self.assertEqual(updated.source_id, source.source_id)
            self.assertEqual(updated.relative_path, "project/renamed.md")


if __name__ == "__main__":
    unittest.main()
