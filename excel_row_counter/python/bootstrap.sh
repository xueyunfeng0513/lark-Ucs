#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" -m pip install -r requirements.txt
# Same invocation style as echo_bot/python; prefer long connection for testing.
export TRANSPORT="${TRANSPORT:-websocket}"
exec "$python_bin" -m excel_counter "$@"
