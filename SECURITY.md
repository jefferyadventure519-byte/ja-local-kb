# Security boundary

- Obsidian Markdown remains the only editable source of truth.
- The runtime only reads explicitly registered Markdown paths inside the
  configured Vault.
- The MCP server exposes search, registered-source retrieval, and status. It
  has no arbitrary path input and no Vault write tool.
- Disabled or removed sources are excluded from both search and direct
  `get_source` access, including old evidence IDs that remain in a derived
  index before cleanup.
- Embedding credentials live in Windows Credential Manager or macOS Keychain
  through `keyring`; they are never written to settings, plugin data, Git, or
  logs.
- STDIO is the only public v1 transport. No TCP listener or remote MCP endpoint
  is created.
- A stale, syncing, failed, missing, or conflicting enabled source blocks all
  retrieval. Old evidence is never returned silently.
- SQLite and LanceDB are device-local derived data and can be rebuilt from the
  registered Markdown.
- Diagnostics must exclude secret values and full source text.
- Release validation scans for credentials and runtime artifacts before
  publication.
