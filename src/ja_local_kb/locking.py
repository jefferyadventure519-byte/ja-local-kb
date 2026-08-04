"""Cross-process runtime lock shared by watcher, CLI, and MCP."""

from __future__ import annotations

from pathlib import Path

import portalocker


class RuntimeLock:
    def __init__(self, runtime_root: Path, timeout: float = 30.0) -> None:
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.path = runtime_root / "runtime.lock"
        self.timeout = timeout

    def exclusive(self):
        return portalocker.Lock(
            self.path,
            mode="a",
            timeout=self.timeout,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
