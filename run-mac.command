#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .local/mac-env.sh ]]; then
  echo "Missing .local/mac-env.sh; configure this Mac first." >&2
  exit 1
fi
source .local/mac-env.sh
for tool in "$FILM_PYTHON" "$FILM_CODEX" "$FILM_FFMPEG" "$FILM_FFPROBE" "$FILM_HYPERFRAMES"; do
  if [[ ! -x "$tool" ]]; then echo "Missing or moved executable: $tool" >&2; exit 1; fi
done
exec "$FILM_PYTHON" app.py --workspace "$FILM_WORKSPACE" "$@"
