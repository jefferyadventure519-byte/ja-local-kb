# Source Freshness State Machine

```text
not_indexed
    │ first sync
    ▼
syncing ── success ──► fresh
   │                     │
   │ failure             │ file event or hash drift
   ▼                     ▼
failed ◄────────────── stale

missing            identity_conflict
```

## State rules

- `not_indexed`: registered but no committed chunks.
- `syncing`: change detected and update is running. Retrieval is blocked.
- `fresh`: current file hash and committed index hash match.
- `stale`: current file hash differs from the committed hash.
- `failed`: parsing, embedding, or index commit failed.
- `missing`: registered path does not exist.
- `identity_conflict`: stable identity or registered path is ambiguous.
- `disabled`: excluded by the device owner and ignored by retrieval readiness.

## Commit boundary

SQLite is set to `syncing` before parsing or embedding. LanceDB rows for the
source are replaced only after all required embeddings succeed. SQLite changes
to `fresh` only after LanceDB row count and hashes pass validation. A crash or
failure leaves the source non-fresh, so old rows cannot be returned.

This is an externally fail-closed two-store protocol, not a single ACID
transaction across SQLite and LanceDB. Full rebuild uses a staging index,
validation, backups, a locked swap, and rollback on failure.
