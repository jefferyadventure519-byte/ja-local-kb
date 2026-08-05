#!/usr/bin/env bash
set -euo pipefail

vault_root="${1:-}"
install_root="${2:-$HOME/Library/Application Support/JA Local KB}"

if [[ -z "$vault_root" ]]; then
  echo "usage: preflight-macos.sh /path/to/vault [install-root]" >&2
  exit 2
fi

ok=true
architecture="$(uname -m)"
architecture_supported=false
if [[ "$architecture" == "arm64" ]]; then
  architecture_supported=true
else
  ok=false
fi
[[ -d "$vault_root" ]] || ok=false
command -v uv >/dev/null 2>&1 || ok=false
command -v git >/dev/null 2>&1 || ok=false
free_kb="$(df -Pk "$(dirname "$install_root")" | awk 'NR==2 {print $4}')"
[[ "${free_kb:-0}" -ge 5242880 ]] || ok=false
legacy_collision=false
if [[ -d "$install_root/data/lancedb" && \
      ! -f "$install_root/app/current.json" ]]; then
  legacy_collision=true
  ok=false
fi

printf 'ok=%s\n' "$ok"
printf 'platform=macos\n'
printf 'architecture=%s\n' "$architecture"
printf 'architecture_supported=%s\n' "$architecture_supported"
printf 'macos_version=%s\n' "$(sw_vers -productVersion)"
printf 'install_root=%s\n' "$install_root"
printf 'vault_root=%s\n' "$vault_root"
printf 'uv=%s\n' "$(command -v uv || true)"
printf 'git=%s\n' "$(command -v git || true)"
printf 'free_space_gb=%s\n' "$((free_kb / 1024 / 1024))"
printf 'legacy_path_collision=%s\n' "$legacy_collision"
printf 'planned_write_1=%s\n' "$install_root"
printf 'planned_write_2=%s\n' "$vault_root/.obsidian/plugins/ja-local-kb"

[[ "$ok" == true ]]
