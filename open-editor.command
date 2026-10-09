#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

legacy=0
legacy_args=()
for arg in "$@"; do
  if [[ "$arg" == "--legacy-server" ]]; then
    legacy=1
  else
    legacy_args+=("$arg")
    if [[ "$arg" == "--no-browser" ]]; then legacy=1; fi
  fi
done

if (( legacy )); then
  if [[ ! -f .local/mac-env.sh ]]; then
    echo "Legacy service mode needs .local/mac-env.sh. Use the native FilmEditor.app for normal editing." >&2
    exit 1
  fi
  source .local/mac-env.sh
  exec "$FILM_PYTHON" scripts/manage_mac.py start "${legacy_args[@]}"
fi

if (( $# )); then
  echo "Usage: open-editor.command [--legacy-server [--no-browser]]" >&2
  exit 2
fi

app="$HOME/Applications/FilmEditor.app"
if [[ ! -d "$app" ]]; then app="$(pwd)/build/FilmEditor.app"; fi
if [[ ! -d "$app" ]]; then
  echo "FilmEditor.app is not built yet. Follow the build instructions in README.md." >&2
  exit 1
fi
exec /usr/bin/open "$app"
