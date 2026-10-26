#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
/usr/bin/python3 -m unittest discover -s tests -v
if [[ "${1:-}" == "--desktop" ]]; then
  /usr/bin/python3 scripts/test-yolo-ui.py
  /usr/bin/python3 scripts/test-redesign-ui.py
  /usr/bin/python3 scripts/test-model-ui.py
  /usr/bin/python3 scripts/test-ui.py
  /usr/bin/python3 scripts/test-conversation-ui.py
  /usr/bin/python3 scripts/test-conversation-ui.py --compact
  /usr/bin/python3 scripts/test-accessibility-control.py
  /usr/bin/python3 scripts/test-physical-workflow.py
fi
