#!/usr/bin/env bash
set -euo pipefail

install_root="${1:-$HOME/Library/Application Support/JA Local KB}"
python="$install_root/runtime/venv/bin/python"
settings="$install_root/config/settings.json"

[[ -x "$python" ]] || {
  echo "installed Python is missing: $python" >&2
  exit 2
}
[[ -f "$settings" ]] || {
  echo "settings are missing: $settings" >&2
  exit 2
}
"$python" -m ja_local_kb.cli --settings "$settings" doctor
