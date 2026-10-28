#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
buddy_python=/usr/bin/python3
if [[ -x .venv/bin/python ]]; then buddy_python=.venv/bin/python; fi
"$buddy_python" -m unittest discover -s tests -v
if [[ "${1:-}" == "--desktop" ]]; then
  "$buddy_python" scripts/test-concurrent-ui.py
  "$buddy_python" scripts/test-computer-access-ui.py
  "$buddy_python" scripts/test-command-review-ui.py
  "$buddy_python" scripts/test-file-ui.py
  "$buddy_python" scripts/test-jane-ui.py
  "$buddy_python" scripts/test-first-use-ui.py
  "$buddy_python" scripts/test-mcp-ui.py
  "$buddy_python" scripts/test-yolo-ui.py
  "$buddy_python" scripts/test-redesign-ui.py
  "$buddy_python" scripts/test-model-ui.py
  "$buddy_python" scripts/test-appearance-ui.py
  "$buddy_python" scripts/test-theme-surfaces-ui.py
  "$buddy_python" scripts/test-ui.py
  "$buddy_python" scripts/test-conversation-ui.py
  "$buddy_python" scripts/test-conversation-ui.py --compact
  "$buddy_python" scripts/test-screenshot-ui.py
  "$buddy_python" scripts/test-accessibility-control.py
  "$buddy_python" scripts/test-accessible-text-ui.py
  "$buddy_python" scripts/test-rejected-text-ui.py
  "$buddy_python" scripts/test-pointer-target-ui.py
  "$buddy_python" scripts/test-physical-workflow.py
fi
