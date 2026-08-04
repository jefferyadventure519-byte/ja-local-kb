# JA Local Knowledge for Obsidian

Desktop-only control surface for the local knowledge runtime.

- Shows every explicitly registered source and its freshness state.
- Adds, updates, disables, or removes the active Markdown file from tracking.
- Registers selected Markdown immediately, closes the modal, and completes
  chunking and embedding through a serialized background queue.
- Marks registered notes with the white J file-tree treatment and a managed
  blue native Graph color group without editing Markdown.
- Starts incremental synchronization, reconciles changes made while offline, and
  automatically restarts a failed watcher with bounded backoff.
- Provides separate Codex and Claude cards with complete review-first connection
  prompts; connection is never labeled successful without real MCP tool calls.
- Separates ad hoc evidence diagnostics from a bounded formal recall report and
  marks historical results stale when their corpus or retrieval baseline changes.
- Never stores the Embedding API key and never edits source Markdown.
- Does not answer questions; Claude and Codex retrieve evidence through MCP.

The release payload is `manifest.json`, `main.js`, and `styles.css`.
