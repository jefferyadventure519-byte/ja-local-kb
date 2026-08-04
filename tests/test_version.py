from __future__ import annotations

import json
import tomllib
from pathlib import Path

import ja_local_kb


def test_runtime_version_matches_project_metadata() -> None:
    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert ja_local_kb.__version__ == metadata["project"]["version"]


def test_stable_release_versions_are_consistent() -> None:
    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (project_root / "obsidian-plugin" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    package = json.loads(
        (project_root / "obsidian-plugin" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    versions = json.loads(
        (project_root / "obsidian-plugin" / "versions.json").read_text(
            encoding="utf-8"
        )
    )

    expected = "0.1.0"
    assert metadata["project"]["version"] == expected
    assert ja_local_kb.__version__ == expected
    assert manifest["version"] == expected
    assert package["version"] == expected
    assert expected in versions
