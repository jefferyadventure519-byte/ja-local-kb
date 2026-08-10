# MCP v1 Contract

The server exposes exactly the three tools below over local STDIO. Every tool
uses one structured envelope:

```json
{"ok": true, "result": {}}
```

or:

```json
{
  "ok": false,
  "error": {"code": "stale", "message": "...", "details": {}}
}
```

## `search_knowledge`

Inputs:

- `query` (required);
- `mode` (default `recall`): `keyword`, `vector`, `hybrid`, `smart`, `recall`,
  or `quality`;
- `top_k`;
- optional `project_ids`;
- optional `client_ids`;
- optional `include_candidates` (default `false`): only valid with `quality`.

Returns evidence only: source text, path, heading, project/client metadata,
document role, source status, update time, scores, index version, and
freshness.

All six modes apply identical project/client filtering. `project_ids` and
`client_ids` use AND when both are present; an absent or empty list adds no
filter. The response echoes `project_ids`, `client_ids`, and the normalized
`filters` that were actually applied.

`quality` first keeps a broad cross-query candidate pool, then calls the
configured independent Reranker and returns the highest-ranked diverse evidence
to the Agent. It never silently falls back to `smart`: a missing or unavailable
Reranker returns `reranker_unavailable`.

When explicitly using `quality`, an Agent may set `include_candidates=true`.
The normal `evidence` remains the compressed Reranker result, while
`candidate_evidence` additionally exposes the complete configured candidate
pool for diagnosis.

Every query is constrained to the current enabled source IDs. A removed or
disabled source cannot reappear through historical LanceDB rows.

`recall` is the default and returns the configured high-recall candidate pool
directly, without calling a Reranker. A capable Agent performs the final
evidence selection, citation, and synthesis.

## `get_source`

Inputs:

- `source_id` or `evidence_id`;
- optional context character limit.

Only currently enabled registered sources may be read. Arbitrary filesystem
paths are forbidden. Historical evidence IDs from disabled or removed sources
are rejected.

## `get_knowledge_status`

Returns source counts by state, embedding fingerprint, index version, last
successful sync, errors, and recovery actions. Secrets are never returned.

## Errors

- `stale`
- `syncing`
- `missing`
- `identity_conflict`
- `embedding_unavailable`
- `reranker_unavailable`
- `not_indexed`
- `invalid_request`

The MCP server does not generate business conclusions or final answers.
