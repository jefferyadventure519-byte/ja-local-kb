"""Settings loading with paths resolved relative to the settings file."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ConfigurationError
from .models import AppSettings


def load_settings(path: Path) -> AppSettings:
    settings_path = path.expanduser().resolve(strict=True)
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        base = settings_path.parent
        for field in (
            "vault_root",
            "runtime_root",
            "source_registry",
            "state_path",
            "lancedb_root",
        ):
            raw_value = payload.get(field)
            if raw_value is None:
                continue
            candidate = Path(raw_value).expanduser()
            if not candidate.is_absolute():
                candidate = base / candidate
            payload[field] = candidate.resolve(strict=False)
        return AppSettings.model_validate(payload)
    except Exception as exc:
        raise ConfigurationError(
            f"Invalid settings file: {settings_path}",
            details={"path": str(settings_path), "error": str(exc)},
        ) from exc
