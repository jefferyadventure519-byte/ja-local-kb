#!/usr/bin/env bash
set -euo pipefail

install_root="$HOME/Library/Application Support/JA Local KB"
vault_root=""
version=""
source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
confirmed=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-root) install_root="$2"; shift 2 ;;
    --vault) vault_root="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --source-root) source_root="$2"; shift 2 ;;
    --confirm) confirmed=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$confirmed" == true && -n "$vault_root" && -n "$version" ]] || {
  echo "update requires --vault, --version, and --confirm" >&2
  exit 2
}

current_file="$install_root/app/current.json"
config_root="$install_root/config"
runtime_python="$install_root/runtime/venv/bin/python"
config_lifecycle="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/config-lifecycle.py"
[[ -x "$runtime_python" ]] || {
  echo "installed Python is unavailable: $runtime_python" >&2
  exit 2
}
[[ -f "$config_lifecycle" ]] || {
  echo "config lifecycle helper is unavailable: $config_lifecycle" >&2
  exit 2
}
source_state="$("$runtime_python" "$config_lifecycle" inspect --config-root "$config_root")"
source_hash="$("$runtime_python" -c \
  'import json,sys; print(json.loads(sys.argv[1])["registry"]["sha256"])' \
  "$source_state")"
if [[ -f "$current_file" && -d "$config_root" ]]; then
  current_version="$(
    python3 -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' \
      "$current_file"
  )"
  [[ "$current_version" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "unsafe installed version identifier: $current_version" >&2
    exit 2
  }
  version_config="$install_root/backups/versions/$current_version/config"
  if [[ ! -d "$version_config" ]]; then
    "$runtime_python" "$config_lifecycle" snapshot \
      --config-root "$config_root" \
      --snapshot-config "$version_config"
  fi
fi

stamp="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$install_root/backups/update_$stamp"
[[ ! -d "$install_root/config" ]] || \
  cp -R "$install_root/config" "$install_root/backups/update_$stamp/config"
[[ ! -f "$install_root/app/current.json" ]] || \
  cp "$install_root/app/current.json" "$install_root/backups/update_$stamp/"
bash "$source_root/scripts/install-macos.sh" \
  --install-root "$install_root" \
  --vault "$vault_root" \
  --version "$version" \
  --source-root "$source_root" \
  --confirm
"$runtime_python" "$config_lifecycle" inspect \
  --config-root "$config_root" \
  --expected-source-hash "$source_hash"
