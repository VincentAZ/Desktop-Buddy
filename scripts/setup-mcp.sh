#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ! -x .venv/bin/python ]]; then
  /usr/bin/python3 -m venv --system-site-packages --without-pip .venv
fi
/usr/bin/python3 -m pip --python .venv/bin/python install -e '.[mcp]'
echo 'MCP support installed. Restart Desktop Buddy using ./launch.sh.'
