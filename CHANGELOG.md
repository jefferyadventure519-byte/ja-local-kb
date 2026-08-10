# Changelog

## Unreleased

## 0.1.1 - 2026-08-10

### Features

- Added backward-compatible customer source enrollment and optional
  `client_ids` filtering to the existing three-tool MCP surface without
  changing Embedding, Reranker, chunking, or existing project source IDs.
- Added repeatable CLI `--client-id`, `source add-client`, Obsidian customer
  Frontmatter recognition, 00-06 admission, archive exclusion, and explicit
  `客户｜客户简称` source grouping.
- Added Apple Silicon distribution smoke coverage for installation, plugin
  payload, STDIO MCP discovery, repeated installation, and user-state
  preservation; Intel Mac remains unsupported.

### Compatibility

- Kept SourceRegistry schema version 1, preserved existing project source IDs,
  and required no migration or re-embedding for unchanged sources.
- Kept the public Agent surface at exactly three MCP tools and protected the
  device-owned source registry during update and rollback.

## 0.1.0 - 2026-08-04

- Published the first reviewed public release without an open-source license;
  local Vault content, credentials, SQLite, LanceDB, logs, and device settings
  remain outside the repository and every Release artifact.
- Froze the Windows-validated dev.12 retrieval core, Obsidian control surface,
  three-tool STDIO MCP contract, incremental watcher, and protected
  update/rollback lifecycle as `0.1.0`.
- Replaced device-owner paths and private-repository wording with portable
  defaults and the reviewed public stable-Release URL.
- Added release regression gates for version consistency, public-source
  hygiene, ignored runtime artifacts, and the no-open-source-license boundary.
- Kept macOS scripts available for the next real-device acceptance; macOS and
  Claude client validation are not claimed by this release.

## 0.1.0-dev.12 - 2026-08-01

- Made the entire enrolled-project section collapsible, retained per-project
  expansion, and persisted both levels across status updates and reloads.
- Replaced the oversized Agent cards with one collapsed first-setup section,
  two compact Codex/Claude rows, and a secondary generic STDIO entry.
- Added a clear Windows/macOS interface font stack and removed unreadable
  8-9px effective text from the sidebar and advanced dialogs.
- Applied one responsive modal shell to every plugin dialog, wrapped long paths
  and prompts, and removed whole-dialog horizontal scrolling.
- Stopped unchanged polling results from rebuilding the sidebar; meaningful
  updates now preserve scroll position and all disclosure states.

## 0.1.0-dev.11 - 2026-08-01

- Replaced the equal-weight advanced button grid with direct Codex and Claude
  connection cards, full review-first prompts, and a secondary generic STDIO
  MCP path.
- Made Agent prompts detect the real environment, preserve existing MCPs,
  require explicit approval before writes, back up configuration, and verify
  actual tool discovery plus status/search/source calls before claiming success.
- Moved ad hoc search into a clearly bounded retrieval diagnostic that renders
  readable evidence cards and never presents returned candidates as proof of
  exhaustive recall.
- Added a bounded recall-quality report with corpus, index, embedding, chunking,
  and retrieval freshness checks; the historical 42-source 47/47 result is now
  explicitly marked outdated against a changed corpus.
- Redesigned system checks, system information, technical-detail disclosure,
  contextual pending-document sync, and the rebuild danger zone to match the
  low-saturation Obsidian control surface.
- Added corpus fingerprints to newly generated benchmark reports and regression
  coverage for connection safety and report freshness decisions.

## 0.1.0-dev.10 - 2026-07-31

- Separated device-owned `sources.json` from version-paired configuration
  snapshots on Windows and macOS.
- Made rollback ignore legacy snapshot copies of `sources.json`, preserve the
  current registry hash, and reject target settings that point elsewhere.
- Added a shared fail-closed lifecycle helper plus isolated regression coverage
  for snapshot exclusion, legacy restore, path mismatch, and hash mismatch.
- Kept timestamped full-config backups for explicit disaster recovery without
  allowing them to become an implicit program-version rollback.

## 0.1.0-dev.9 - 2026-07-31

- Changed Obsidian enrollment to persist source registration first, close the
  modal, and complete chunking plus embedding in the background.
- Serialized watcher synchronization through one worker queue and limited
  registry-change work to sources whose hashes are actually stale.
- Added a compact background progress card with batch completion, active file,
  waiting count, and failure state.
- Added a plugin-managed native Obsidian Graph color group for registered
  sources while preserving user groups and leaving Markdown untouched; open
  Graph views now refresh in place instead of requiring a close/reopen cycle.
- Added restart-safe batch state, duplicate-submit protection, Windows/macOS
  installer defaults, and regression coverage for queue serialization.

## 0.1.0-dev.8 - 2026-07-31

- Added Obsidian file and multi-file context-menu enrollment, plus explicit
  direct-child-only Markdown selection for folders.
- Made original-file deletion permanently purge the source registry, SQLite
  freshness state, chunks, and LanceDB rows; restoring the file now requires
  deliberate re-enrollment.
- Preserved stable source identity across file and folder renames while
  synchronizing the new path into the derived index.
- Rebuilt the right-side control panel around health, current document, and
  project-level status, with secondary maintenance actions collapsed.
- Added a binary indexed-state marker to Obsidian's file tree using the
  approved white transparent J asset and a low-saturation blue fade.
- Included the new icon asset in both Windows and macOS installation packages.

## 0.1.0-dev.7 - 2026-07-30

- Added immediate registered-source hash reconciliation when the watcher starts,
  so edits made while Obsidian was closed do not wait for the periodic scan.
- Made unexpected file-system observer termination fail visibly to the Obsidian
  supervisor instead of being treated as a clean watcher stop.
- Added bounded automatic watcher restart in the Obsidian plugin with
  1/3/10/30/60-second backoff and clean cancellation on manual stop or unload.
- Added real file-event regression coverage through changed-chunk re-embedding,
  unchanged-vector reuse, local vector commit, and final fresh status.
- Added a package-version consistency regression gate after the rejected dev.6
  deployment exposed a stale internal version constant.

## 0.1.0-dev.5 - 2026-07-30

- Added `recall` as the primary Agent mode: Qwen3 Embedding produces an
  80-item traceable evidence pool without invoking the optional Reranker.
- Added an isolated quality-retrieval path with an 80-item cross-route
  candidate pool, independent SiliconFlow Reranker, and 24-40 item evidence
  handoff to external Agents.
- Added candidate-stage and rerank-stage benchmark metrics, Reranker
  fingerprints, explicit unavailable errors, and regression coverage.
- Kept `quality` as an optional compact Top40 mode for clients with tighter
  context budgets.
- Passed the frozen 12-question recall benchmark at 47/47 evidence groups,
  27/27 projects, and 100% traceability; API latency remains a separately
  reported non-passing gate.
- Verified the real STDIO MCP default, 80-item handoff, and evidence-ID source
  round trip.

## 0.1.0-dev.4 - 2026-07-30

- Added deterministic complex-query decomposition, enumerated-focus routing,
  write-safety guard expansion, route-weighted evidence quotas, and source-level
  deduplication.
- Added reusable HTTPS connections and a rebuildable in-memory vector matrix
  with batched distance calculation; LanceDB remains the persistent index.
- Added hash-bound benchmark corrections, corpus validation, candidate
  diagnostics, and latency profiling.
- Passed the frozen 12-question 4B benchmark at 89.36% evidence coverage,
  96.30% project coverage, 100% traceability, and 1.165-second complex-query P95.
- Retained the isolated 8B profile as a non-default comparison after its
  27.60-second formal P95 failed the latency gate.
- Increased the automated Python regression suite to 38 tests.
- Aligned the installed package version, release pointer, and doctor output.

## 0.1.0-dev - 2026-07-29

- Added explicit source registry with path-independent stable identities.
- Added structure-aware Markdown chunking and incremental API embeddings.
- Added SQLite freshness ledger and LanceDB keyword/vector/hybrid retrieval.
- Added fail-closed stale gate and cross-process runtime locking.
- Added three-tool local STDIO MCP server.
- Added desktop Obsidian status and source-control plugin.
- Added Windows/macOS preflight, install, doctor, update, and rollback protocol.
- Added legacy-index collision blocking and configuration-preserving lifecycle
  smoke tests.
- Added disabled/removed-source protection for direct evidence lookup.
