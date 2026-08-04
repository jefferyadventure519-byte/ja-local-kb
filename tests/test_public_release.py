from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_public_text_does_not_contain_device_owner_paths() -> None:
    roots = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "INSTALL_AGENT.md",
        PROJECT_ROOT / "UPDATE_AGENT.md",
        PROJECT_ROOT / "SECURITY.md",
        PROJECT_ROOT / "CHANGELOG.md",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "scripts",
        PROJECT_ROOT / "src",
        PROJECT_ROOT / "docs",
        PROJECT_ROOT / "tests",
        PROJECT_ROOT / "config",
        PROJECT_ROOT / "obsidian-plugin" / "src",
        PROJECT_ROOT / "obsidian-plugin" / "manifest.json",
        PROJECT_ROOT / "obsidian-plugin" / "main.js",
        PROJECT_ROOT / "obsidian-plugin" / "package.json",
        PROJECT_ROOT / "obsidian-plugin" / "versions.json",
    )
    slash = "\\"
    forbidden = (
        f"D:{slash}Claude" + "Project",
        f"D:{slash}Bl" + "vckKnowledgeRuntime",
        f"C:{slash}Users{slash}E" + "DY",
        "未经 Bl" + "vck 确认",
        "\u79c1\u6709 GitHub",
    )

    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        else:
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.name != "test_public_release.py"
                and path.suffix.lower()
                in {".md", ".py", ".ps1", ".sh", ".json", ".toml", ".js"}
            )

    violations: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)}: {marker}")
    assert not violations, "\n".join(violations)


def test_public_text_has_no_high_confidence_credentials() -> None:
    patterns = (
        re.compile(r"gh[opurs]_[A-Za-z0-9]{20,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )
    excluded = {"test_public_release.py", "package-lock.json", "uv.lock"}
    violations: list[str] = []

    scan_roots = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "INSTALL_AGENT.md",
        PROJECT_ROOT / "UPDATE_AGENT.md",
        PROJECT_ROOT / "SECURITY.md",
        PROJECT_ROOT / "CHANGELOG.md",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "scripts",
        PROJECT_ROOT / "src",
        PROJECT_ROOT / "docs",
        PROJECT_ROOT / "tests",
        PROJECT_ROOT / "config",
        PROJECT_ROOT / "obsidian-plugin" / "src",
        PROJECT_ROOT / "obsidian-plugin" / "manifest.json",
        PROJECT_ROOT / "obsidian-plugin" / "main.js",
        PROJECT_ROOT / "obsidian-plugin" / "package.json",
        PROJECT_ROOT / "obsidian-plugin" / "versions.json",
    )
    for root in scan_roots:
        paths = (root,) if root.is_file() else root.rglob("*")
        for path in paths:
            if (
                not path.is_file()
                or path.name in excluded
                or any(
                    part.startswith(".")
                    for part in path.relative_to(PROJECT_ROOT).parts
                )
                or path.suffix.lower()
                not in {
                    ".md",
                    ".py",
                    ".ps1",
                    ".sh",
                    ".json",
                    ".toml",
                    ".ts",
                    ".js",
                }
            ):
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                if pattern.search(text):
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}: {pattern.pattern}"
                    )
    assert not violations, "\n".join(violations)


def test_runtime_and_secret_artifacts_are_ignored() -> None:
    ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    required = (
        "runtime/",
        "releases/",
        "state/",
        "data/",
        "config/settings.json",
        "config/sources.json",
        "obsidian-plugin/data.json",
        "*.sqlite3",
        "lancedb/",
        "logs/",
        "backups/",
        ".env.*",
        "*.pem",
    )
    assert all(pattern in ignore for pattern in required)


def test_repository_has_no_open_source_license_file() -> None:
    license_files = tuple(PROJECT_ROOT.glob("LICENSE*"))
    assert license_files == ()
    metadata = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = { text = "Proprietary" }' in metadata
