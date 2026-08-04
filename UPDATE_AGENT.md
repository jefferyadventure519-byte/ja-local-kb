# Agent update and rollback protocol

1. Read the target release notes and schema notes.
2. Run the platform preflight against the existing installation.
3. Back up the complete `config`, current-version pointer, Obsidian plugin
   files, and any client configuration that will be edited. This timestamped
   backup is for explicit disaster recovery. The version-paired snapshot saves
   version-sensitive settings but deliberately excludes device-owned
   `sources.json`.
4. Install the new stable release without deleting state or Vault content.
5. Run doctor, MCP tool discovery, status, incremental sync, and one retrieval
   smoke test.
6. Keep the previous release source under `releases/`.
7. If validation fails, run the rollback script for the previous version. The
   rollback reinstalls code and plugin assets and restores version-sensitive
   settings when available. It ignores any legacy snapshot copy of
   `sources.json`, verifies the live registry hash before and after restore,
   and rejects target settings that point to a different registry path.
8. A release with an irreversible SQLite migration requires a separate owner
   confirmation before update.
