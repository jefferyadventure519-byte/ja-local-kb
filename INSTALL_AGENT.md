# Agent installation protocol

This protocol is intentionally review-first. The Agent must not run an install
script until the device owner has reviewed preflight output and confirmed the
target paths.

## Windows

1. Download a reviewed stable Release from
   <https://github.com/jefferyadventure519-byte/ja-local-kb/releases> or check
   out its exact tag into a temporary D-drive folder. Do not install from
   `main`.
2. Run:

   ```powershell
   .\scripts\preflight-windows.ps1 `
     -InstallRoot "D:\chosen\ja-local-kb-root" `
     -VaultRoot "D:\path\to\vault"
   ```

3. Show the owner the target root, Vault, architecture, free space, missing
   dependencies, legacy-path collision result, and all planned writes. If
   preflight reports an unowned existing index, stop. Do not migrate, overwrite,
   or reuse that root without a separately approved migration plan.
4. After explicit approval:

   ```powershell
   .\scripts\install-windows.ps1 `
     -InstallRoot "D:\chosen\ja-local-kb-root" `
     -VaultRoot "D:\path\to\vault" `
     -ConfirmInstall
   ```

   To create the approved high-recall configuration using one SiliconFlow
   credential:

   ```powershell
   .\scripts\install-windows.ps1 `
     -InstallRoot "D:\chosen\ja-local-kb-root" `
     -VaultRoot "D:\path\to\vault" `
     -EmbeddingProvider openai_compatible `
     -EmbeddingBaseUrl "https://api.siliconflow.cn/v1" `
     -EmbeddingModel "Qwen/Qwen3-Embedding-8B" `
     -EmbeddingDimensions 4096 `
     -RerankerModel "Qwen/Qwen3-Reranker-8B" `
     -ConfirmInstall
   ```

   New installations default to `recall`: the 8B Embedding candidate pool is
   returned directly to the Agent. Supplying a Reranker additionally enables
   the optional compact `quality` mode.

5. Store the API key interactively:

   ```powershell
   D:\chosen\ja-local-kb-root\runtime\venv\Scripts\python.exe `
     -m ja_local_kb.cli `
     --settings D:\chosen\ja-local-kb-root\config\settings.json `
     set-secret
   ```

6. Enable `ja-local-kb` in Obsidian, explicitly add sources, and sync them.
7. In the Obsidian control panel, use the Codex or Claude card to copy the
   complete connection prompt. The target Agent must inspect the real device,
   show its plan, wait for explicit approval, preserve existing MCP entries,
   back up the client file, and verify actual tool discovery. For manual
   configuration generation only:

   ```powershell
   D:\chosen\ja-local-kb-root\runtime\venv\Scripts\python.exe `
     -m ja_local_kb.cli `
     --settings D:\chosen\ja-local-kb-root\config\settings.json `
     connection-config --client codex
   ```

   Repeat with `--client claude`. Back up the existing client file before
   merging the single server entry. Restart the client after review. A written
   configuration alone is not proof of connection: call status, search, and
   source retrieval through the discovered MCP before declaring success.
8. Run `scripts\doctor-windows.ps1` and perform one MCP status call and one
   source-grounded search.

## macOS

1. Download a reviewed stable Release from
   <https://github.com/jefferyadventure519-byte/ja-local-kb/releases> or check
   out its exact tag. Do not install from `main`.
2. Run:

   ```bash
   ./scripts/preflight-macos.sh "/path/to/vault"
   ```

3. Show the owner the preflight result and planned writes.
   If `legacy_path_collision=true`, stop and choose a new root or obtain
   approval for a separate migration.
4. After explicit approval:

   ```bash
   ./scripts/install-macos.sh --vault "/path/to/vault" --confirm
   ```

   The equivalent high-recall options are:

   ```bash
   ./scripts/install-macos.sh \
     --vault "/path/to/vault" \
     --embedding-provider openai_compatible \
     --embedding-base-url "https://api.siliconflow.cn/v1" \
     --embedding-model "Qwen/Qwen3-Embedding-8B" \
     --embedding-dimensions 4096 \
     --reranker-model "Qwen/Qwen3-Reranker-8B" \
     --confirm
   ```

5. Use the printed command to store the API key in Keychain.
6. Enable the Obsidian plugin, choose sources, and sync.
7. Generate Codex and Claude configuration snippets, back up existing client
   configuration, merge only the reviewed server entry, and restart clients.
8. Run `scripts/doctor-macos.sh`.

## Non-goals

- Do not copy any Vault, SQLite, LanceDB, log, or secret into the repository.
- Do not install from `main`; use a reviewed stable version.
- Do not overwrite existing client configuration wholesale.
- Do not enroll folders or files that the device owner did not select.
