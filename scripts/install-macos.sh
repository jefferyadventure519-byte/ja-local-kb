#!/usr/bin/env bash
set -euo pipefail

vault_root=""
install_root="$HOME/Library/Application Support/JA Local KB"
version="0.1.0"
embedding_model="text-embedding-3-large"
embedding_provider="openai"
embedding_base_url="https://api.openai.com/v1"
embedding_dimensions=""
reranker_model=""
reranker_base_url="https://api.siliconflow.cn/v1"
reranker_secret_name="embedding_api_key"
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
confirmed=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault) vault_root="$2"; shift 2 ;;
    --install-root) install_root="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --embedding-model) embedding_model="$2"; shift 2 ;;
    --embedding-provider) embedding_provider="$2"; shift 2 ;;
    --embedding-base-url) embedding_base_url="$2"; shift 2 ;;
    --embedding-dimensions) embedding_dimensions="$2"; shift 2 ;;
    --reranker-model) reranker_model="$2"; shift 2 ;;
    --reranker-base-url) reranker_base_url="$2"; shift 2 ;;
    --reranker-secret-name) reranker_secret_name="$2"; shift 2 ;;
    --source-root) source_root="$2"; shift 2 ;;
    --confirm) confirmed=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$confirmed" == true ]] || {
  echo "installation requires --confirm after preflight review" >&2
  exit 2
}
[[ -n "$vault_root" ]] || {
  echo "--vault is required" >&2
  exit 2
}
bash "$source_root/scripts/preflight-macos.sh" "$vault_root" "$install_root"

release_root="$install_root/releases/$version"
runtime_root="$install_root/runtime"
config_root="$install_root/config"
state_root="$install_root/state"
data_root="$install_root/data"
backup_root="$install_root/backups"
plugin_root="$vault_root/.obsidian/plugins/ja-local-kb"
venv_root="$runtime_root/venv"
python="$venv_root/bin/python"

mkdir -p \
  "$release_root" \
  "$runtime_root" \
  "$config_root" \
  "$state_root" \
  "$data_root" \
  "$install_root/logs" \
  "$backup_root" \
  "$install_root/app"

if [[ "$(cd "$source_root" && pwd)" != "$(cd "$release_root" && pwd)" ]]; then
  for directory in src contracts docs scripts; do
    if [[ ! -e "$release_root/$directory" ]]; then
      cp -R "$source_root/$directory" "$release_root/$directory"
    fi
  done
  for file in \
    pyproject.toml uv.lock README.md SECURITY.md CHANGELOG.md \
    INSTALL_AGENT.md UPDATE_AGENT.md; do
    cp "$source_root/$file" "$release_root/$file"
  done
fi
mkdir -p "$release_root/obsidian-plugin"
if [[ "$(cd "$source_root" && pwd)" != "$(cd "$release_root" && pwd)" ]]; then
  for file in \
    main.js manifest.json styles.css versions.json j-icon-white.png; do
    cp "$source_root/obsidian-plugin/$file" \
      "$release_root/obsidian-plugin/$file"
  done
fi

export UV_PROJECT_ENVIRONMENT="$venv_root"
export UV_CACHE_DIR="$runtime_root/uv-cache"
uv sync \
  --project "$release_root" \
  --extra all \
  --no-dev \
  --no-editable \
  --reinstall-package ja-local-kb

settings_path="$config_root/settings.json"
sources_path="$config_root/sources.json"
if [[ ! -f "$sources_path" ]]; then
  printf '{\n  "schema_version": 1,\n  "sources": []\n}\n' > "$sources_path"
fi
if [[ ! -f "$settings_path" ]]; then
  "$python" - "$settings_path" "$vault_root" "$runtime_root" \
    "$sources_path" "$state_root/state.sqlite3" "$data_root/lancedb" \
    "$embedding_model" "$embedding_provider" "$embedding_base_url" \
    "$embedding_dimensions" "$reranker_model" "$reranker_base_url" \
    "$reranker_secret_name" <<'PY'
import json
import sys
from pathlib import Path

(
    target,
    vault,
    runtime,
    sources,
    state,
    lancedb,
    model,
    provider,
    embedding_base_url,
    dimensions,
    reranker_model,
    reranker_base_url,
    reranker_secret_name,
) = sys.argv[1:]
payload = {
    "schema_version": 1,
    "vault_root": vault,
    "runtime_root": runtime,
    "source_registry": sources,
    "state_path": state,
    "lancedb_root": lancedb,
    "embedding": {
        "provider": provider,
        "model": model,
        "base_url": embedding_base_url,
        "secret_name": "embedding_api_key",
        "dimensions": int(dimensions) if dimensions else None,
        "batch_size": 64,
        "timeout_seconds": 30,
        "max_retries": 3,
    },
    "chunking": {"max_chars": 2200, "overlap_chars": 120},
    "retrieval": {
        "default_mode": "recall",
        "simple_top_k": 8,
        "complex_candidate_k": 24,
        "complex_top_k": 24,
        "smart_candidate_k": 80,
        "smart_max_subqueries": 6,
        "smart_vector_subqueries": 6,
        "recall_candidate_k": 80,
        "quality_candidate_k": 80,
        "quality_top_k": 40,
        "evidence_min": 6,
        "evidence_max": 40,
    },
}
if reranker_model:
    payload["reranker"] = {
        "provider": "siliconflow",
        "model": reranker_model,
        "base_url": reranker_base_url,
        "secret_name": reranker_secret_name,
        "instruction": (
            "Rank each passage by whether it provides specific, current, "
            "directly citable evidence needed to answer the query. Prefer "
            "concrete decisions, rules, constraints, exceptions, state "
            "changes, and source facts over generic topical similarity. "
            "Treat a multi-part query as independent evidence needs: rank a "
            "passage highly when it directly supports any one named project, "
            "sub-question, boundary, or exception, even if it does not answer "
            "the whole query. "
            "Preserve complementary evidence."
        ),
        "timeout_seconds": 120,
        "max_retries": 3,
    }
Path(target).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
fi

if [[ -d "$plugin_root" ]]; then
  stamp="$(date +%Y%m%d_%H%M%S)"
  cp -R "$plugin_root" "$backup_root/obsidian-plugin_$stamp"
fi
mkdir -p "$plugin_root"
for file in main.js manifest.json styles.css j-icon-white.png; do
  cp "$release_root/obsidian-plugin/$file" "$plugin_root/$file"
done
if [[ ! -f "$plugin_root/data.json" ]]; then
  "$python" - "$plugin_root/data.json" "$python" "$settings_path" <<'PY'
import json
import sys
from pathlib import Path

target, python, settings = sys.argv[1:]
Path(target).write_text(
    json.dumps(
        {
            "pythonExecutable": python,
            "settingsPath": settings,
            "autoStartWatcher": True,
            "statusRefreshSeconds": 10,
            "graphHighlightEnabled": True,
            "backgroundBatch": None,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
fi

"$python" - "$install_root/app/current.json" "$version" "$release_root" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

target, version, release = sys.argv[1:]
Path(target).write_text(
    json.dumps(
        {
            "version": version,
            "release_root": release,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

printf 'installed=true\n'
printf 'version=%s\n' "$version"
printf 'install_root=%s\n' "$install_root"
printf 'python=%s\n' "$python"
printf 'settings=%s\n' "$settings_path"
printf 'plugin=%s\n' "$plugin_root"
