"""Debounced cross-platform watcher for explicitly registered Markdown."""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

from .registry import update_source_path
from .runtime import ServiceManager

LOGGER = logging.getLogger("ja_local_kb.watcher")


def normalized(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


class WatchDaemon:
    def __init__(
        self,
        manager: ServiceManager,
        *,
        debounce_seconds: float = 3.0,
        scan_interval_seconds: float = 60.0,
    ) -> None:
        self.manager = manager
        self.debounce_seconds = debounce_seconds
        self.scan_interval_seconds = scan_interval_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._timer_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._observer: Any | None = None
        self._sync_queue: queue.Queue[str | None] = queue.Queue()
        self._queue_lock = threading.Lock()
        self._queued: set[str] = set()
        self._inflight: set[str] = set()
        self._rerun: set[str] = set()
        self._worker: threading.Thread | None = None

    def run(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            raise RuntimeError("File watching support is not installed") from exc

        daemon = self

        class Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if not event.is_directory:
                    daemon.handle_path(event.src_path)

            def on_created(self, event):
                if not event.is_directory:
                    daemon.handle_path(event.src_path)

            def on_deleted(self, event):
                if not event.is_directory:
                    daemon.handle_path(event.src_path)

            def on_moved(self, event):
                if not event.is_directory:
                    daemon.handle_move(event.src_path, event.dest_path)

        settings = self.manager.get().settings
        observer = Observer()
        handler = Handler()
        observer.schedule(handler, str(settings.vault_root), recursive=True)
        registry_parent = settings.source_registry.parent
        try:
            registry_parent.relative_to(settings.vault_root)
        except ValueError:
            observer.schedule(handler, str(registry_parent), recursive=False)
        self._stop_event.clear()
        self._observer = observer
        observer.start()
        self._start_worker()
        LOGGER.info("Watching %s registered sources", len(self._path_map()))
        try:
            # Reconcile changes that happened while the watcher was offline.
            # Starting the observer first closes the gap between this scan and
            # subsequent file-system events.
            self.scan_registered_hashes()
            while not self._stop_event.is_set():
                observer.join(timeout=self.scan_interval_seconds)
                if self._stop_event.is_set():
                    break
                if not observer.is_alive():
                    raise RuntimeError("File-system observer stopped unexpectedly")
                self.scan_registered_hashes()
        except KeyboardInterrupt:
            self.stop()
        finally:
            if observer.is_alive():
                observer.stop()
            observer.join()
            self._observer = None
            self._cancel_pending()
            self._stop_worker()

    def stop(self) -> None:
        self._stop_event.set()
        observer = self._observer
        if observer is not None and observer.is_alive():
            observer.stop()

    def handle_path(self, changed_path: str | Path) -> None:
        service = self.manager.get()
        if normalized(changed_path) == normalized(service.settings.source_registry):
            self.scan_registered_hashes()
            return
        source_id = self._path_map().get(normalized(changed_path))
        if source_id:
            self.schedule(source_id)

    def handle_move(
        self, source_path: str | Path, destination_path: str | Path
    ) -> None:
        registry_path = self.manager.get().settings.source_registry
        if normalized(source_path) == normalized(registry_path) or normalized(
            destination_path
        ) == normalized(registry_path):
            self.scan_registered_hashes()
            return
        path_map = self._path_map()
        source_id = path_map.get(normalized(source_path))
        if (
            source_id
            and str(destination_path).lower().endswith(".md")
            and self._move_registered_path(source_id, Path(destination_path))
        ):
            self.schedule(source_id)
            return
        destination_id = path_map.get(normalized(destination_path))
        if destination_id:
            self.schedule(destination_id)
        elif source_id:
            self.schedule(source_id)

    def schedule_all(self) -> None:
        for source_id in self._path_map().values():
            self.schedule(source_id)

    def scan_registered_hashes(self) -> None:
        try:
            for item in self.manager.get().detect_drift():
                self.schedule(item["source_id"])
        except Exception:
            LOGGER.exception("Registered-source hash scan failed")

    def schedule(self, source_id: str) -> None:
        with self._timer_lock:
            existing = self._timers.pop(source_id, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(
                self.debounce_seconds,
                self._enqueue,
                args=(source_id,),
            )
            timer.daemon = True
            self._timers[source_id] = timer
            timer.start()

    def _enqueue(self, source_id: str) -> None:
        with self._timer_lock:
            self._timers.pop(source_id, None)
        with self._queue_lock:
            if source_id in self._inflight:
                self._rerun.add(source_id)
                return
            if source_id in self._queued:
                return
            self._queued.add(source_id)
        self._sync_queue.put(source_id)

    def _sync(self, source_id: str) -> None:
        try:
            result = self.manager.get().sync_source(source_id)
            LOGGER.info("Synced source %s: %s", source_id, result)
        except Exception:
            LOGGER.exception("Failed to sync source %s", source_id)

    def _start_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="ja-local-kb-sync-worker",
            daemon=True,
        )
        self._worker.start()

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._sync_queue.put(None)
        worker.join(timeout=5)
        self._worker = None
        with self._queue_lock:
            self._queued.clear()
            self._inflight.clear()
            self._rerun.clear()

    def _worker_loop(self) -> None:
        while True:
            source_id = self._sync_queue.get()
            if source_id is None:
                self._sync_queue.task_done()
                return
            if self._stop_event.is_set():
                with self._queue_lock:
                    self._queued.discard(source_id)
                    self._rerun.discard(source_id)
                self._sync_queue.task_done()
                continue
            with self._queue_lock:
                self._queued.discard(source_id)
                self._inflight.add(source_id)
            try:
                self._sync(source_id)
            finally:
                with self._queue_lock:
                    self._inflight.discard(source_id)
                    rerun = (
                        source_id in self._rerun
                        and not self._stop_event.is_set()
                    )
                    self._rerun.discard(source_id)
                    if rerun:
                        self._queued.add(source_id)
                self._sync_queue.task_done()
                if rerun:
                    self._sync_queue.put(source_id)

    def _cancel_pending(self) -> None:
        with self._timer_lock:
            pending = list(self._timers.values())
            self._timers.clear()
        for timer in pending:
            timer.cancel()

    def _path_map(self) -> dict[str, str]:
        service = self.manager.get()
        return {
            normalized(
                service.settings.vault_root / source.relative_path
            ): source.source_id
            for source in service.registry.sources
            if source.enabled
        }

    def _move_registered_path(
        self,
        source_id: str,
        destination_path: Path,
    ) -> bool:
        service = self.manager.get()
        root = service.settings.vault_root.resolve(strict=True)
        destination = destination_path.resolve(strict=False)
        try:
            relative = destination.relative_to(root).as_posix()
        except ValueError:
            return False
        try:
            update_source_path(
                service.settings.source_registry,
                source_id,
                relative,
            )
        except KeyError:
            return False
        return True
