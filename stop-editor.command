#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
source .local/mac-env.sh
exec "$FILM_PYTHON" scripts/manage_mac.py stop
