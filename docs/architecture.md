# v1 Architecture Contract

## Source of truth

Obsidian Markdown is the only editable knowledge source. SQLite and LanceDB are
derived and rebuildable. The service never writes source Markdown.

## Stable identity

A tracked document is identified by:

```text
source_id = sha256(project_id + client_id + document_role)[:32]
```

The path is mutable metadata. A rename with unchanged content updates path
metadata without re-embedding. Duplicate stable identities stop synchronization
with `identity_conflict`.

## Per-device boundary

Each device owns its own:

- source registry;
- secret reference;
- SQLite state;
- LanceDB;
- logs and backups.

No shared index, central gateway, or remote MCP exists in v1.

## Public interface

The only public Agent interface is local STDIO MCP. Internal CLI and
plugin-to-core IPC are implementation details and are not compatibility
contracts.

## Fail-closed freshness

Before any retrieval, all enabled sources must be `fresh`. A source in
`syncing`, `stale`, `failed`, `missing`, or `identity_conflict` blocks retrieval.
The service never silently falls back to old evidence.

Every search is filtered by the current enabled `source_id` set. Direct source
or evidence lookup also rejects disabled and removed sources, even if old
derived rows have not yet been compacted.

## Embedding space

Document chunks and queries use the same provider, model, dimensions, and
normalization contract. Any change to that fingerprint requires a full vector
rebuild.

The approved quality-first profile uses SiliconFlow Qwen3 Embedding 8B. The
Reranker is configured only for the optional compact path and has an independent
fingerprint.

## Quality retrieval

`recall` is the quality-first Agent path:

1. deterministic multi-route retrieval builds an 80-item candidate pool;
2. the complete source-grounded pool is returned directly;
3. Claude, Codex, or another connected Agent selects evidence, cites sources,
   and produces the final answer.

`quality` remains the optional compact path:

1. deterministic multi-route retrieval builds a broad candidate pool without
   early per-project quotas;
2. an independent API Reranker scores the candidate passages;
3. the service returns 24-40 source-grounded evidence items;
4. Claude, Codex, or another connected Agent performs the final cross-project
   analysis and answer.

The MCP default is the complete `recall` pool. A client may explicitly request
`quality` when context size matters more than preserving every candidate; this
prevents lossy compression from becoming the default recall ceiling.

Candidate recall, reranked recall, Agent citation completeness, and latency are
measured separately. A Reranker fingerprint is independent from the embedding
fingerprint and does not require rebuilding LanceDB.

## Cross-store commit boundary

SQLite and LanceDB do not share one physical database transaction. The runtime
therefore exposes an external fail-closed boundary: mark the source `syncing`,
write and validate all LanceDB rows, then mark SQLite `fresh`. Any exception or
interruption leaves a non-fresh state, so retrieval is blocked until repair or
rebuild.
