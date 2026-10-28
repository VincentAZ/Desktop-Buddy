#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
buddy_python=/usr/bin/python3
if [[ -x .venv/bin/python ]]; then buddy_python=.venv/bin/python; fi
exec "$buddy_python" -m desktop_buddy "$@"
