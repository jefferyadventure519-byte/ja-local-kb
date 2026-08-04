from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "config-lifecycle.py"
SCRIPTS = SCRIPT.parent


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def settings(registry: Path, *, max_chars: int = 2200) -> dict:
    return {
        "schema_version": 1,
        "vault_root": str(registry.parent / "vault"),
        "runtime_root": str(registry.parent / "runtime"),
        "source_registry": str(registry),
        "embedding": {
            "provider": "openai_compatible",
            "model": "test-model",
            "base_url": "https://example.invalid/v1",
            "secret_name": "test_secret",
            "dimensions": 8,
        },
        "chunking": {"max_chars": max_chars, "overlap_chars": 10},
    }


def run_helper(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def make_active_config(root: Path) -> tuple[Path, bytes]:
    config = root / "config"
    registry = config / "sources.json"
    write_json(
        registry,
        {
            "schema_version": 1,
            "sources": [{"source_id": "current-device-source"}],
        },
    )
    write_json(config / "settings.json", settings(registry))
    write_json(config / "settings.profile.json", {"profile": "current"})
    return config, registry.read_bytes()


def test_snapshot_excludes_device_sources_and_restore_ignores_legacy_copy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config, original_registry = make_active_config(root)
        snapshot = root / "snapshot" / "config"

        result = run_helper(
            "snapshot",
            "--config-root",
            str(config),
            "--snapshot-config",
            str(snapshot),
        )
        payload = json.loads(result.stdout)
        assert payload["excluded"] == ["sources.json"]
        assert (snapshot / "settings.json").is_file()
        assert not (snapshot / "sources.json").exists()

        legacy = root / "legacy" / "config"
        write_json(
            legacy / "settings.json",
            settings(config / "sources.json", max_chars=999),
        )
        write_json(
            legacy / "sources.json",
            {"schema_version": 1, "sources": [{"source_id": "old-source"}]},
        )
        expected_hash = hashlib.sha256(original_registry).hexdigest()
        restored = run_helper(
            "restore",
            "--config-root",
            str(config),
            "--snapshot-config",
            str(legacy),
            "--expected-source-hash",
            expected_hash,
        )
        restored_payload = json.loads(restored.stdout)

        assert restored_payload["ignored"] == ["sources.json"]
        assert (config / "sources.json").read_bytes() == original_registry
        active = json.loads((config / "settings.json").read_text(encoding="utf-8"))
        assert active["chunking"]["max_chars"] == 999


def test_validate_rejects_target_with_different_registry_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config, _ = make_active_config(root)
        snapshot = root / "snapshot" / "config"
        write_json(snapshot / "settings.json", settings(root / "other-sources.json"))

        result = run_helper(
            "validate",
            "--config-root",
            str(config),
            "--snapshot-config",
            str(snapshot),
            check=False,
        )

        assert result.returncode == 1
        assert "different source registry" in result.stderr


def test_restore_rejects_changed_registry_before_copying_settings() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config, original_registry = make_active_config(root)
        original_settings = (config / "settings.json").read_bytes()
        snapshot = root / "snapshot" / "config"
        write_json(
            snapshot / "settings.json",
            settings(config / "sources.json", max_chars=999),
        )

        result = run_helper(
            "restore",
            "--config-root",
            str(config),
            "--snapshot-config",
            str(snapshot),
            "--expected-source-hash",
            "0" * 64,
            check=False,
        )

        assert result.returncode == 1
        assert "changed before restore" in result.stderr
        assert (config / "sources.json").read_bytes() == original_registry
        assert (config / "settings.json").read_bytes() == original_settings


def test_inspect_rejects_invalid_registry_schema() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config"
        registry = config / "sources.json"
        write_json(registry, {"schema_version": 1, "sources": "not-a-list"})
        write_json(config / "settings.json", settings(registry))

        result = run_helper(
            "inspect",
            "--config-root",
            str(config),
            check=False,
        )

        assert result.returncode == 1
        assert "Invalid device source registry schema" in result.stderr


def test_platform_update_and_rollback_scripts_use_shared_source_guard() -> None:
    update_windows = (SCRIPTS / "update-windows.ps1").read_text(encoding="utf-8")
    rollback_windows = (SCRIPTS / "rollback-windows.ps1").read_text(
        encoding="utf-8"
    )
    update_macos = (SCRIPTS / "update-macos.sh").read_text(encoding="utf-8")
    rollback_macos = (SCRIPTS / "rollback-macos.sh").read_text(encoding="utf-8")

    for script in (update_windows, rollback_windows, update_macos, rollback_macos):
        assert "config-lifecycle.py" in script
        assert "--expected-source-hash" in script
    for script in (update_windows, update_macos):
        assert "snapshot" in script
    for script in (rollback_windows, rollback_macos):
        assert "validate" in script
        assert "restore" in script

    assert "Copy-Item -LiteralPath $configPath" not in update_windows
    assert "Copy-Item -LiteralPath $configPath" not in rollback_windows
    assert 'cp -R "$config_root" "$version_config"' not in update_macos
    assert 'cp -R "$config_root" "$current_version_config"' not in rollback_macos
