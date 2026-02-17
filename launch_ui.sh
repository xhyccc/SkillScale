#!/usr/bin/env bash
#
# Launch SkillScale Unified UI
#
# Usage:
#   ./launch_ui.sh
#
set -euo pipefail
cd "$(dirname "$0")"

VENV_PYTHON="$(pwd)/.venv/bin/python3"
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Error: Python venv not found at $VENV_PYTHON"
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r ui/requirements.txt"
    exit 1
fi

cleanup() {
    echo ""
    echo "Shutting down UI services..."
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT

echo "Starting SkillScale API on :8401 ..."
"$VENV_PYTHON" -m uvicorn ui.management.server:app \
    --host 0.0.0.0 --port 8401 --log-level info &
sleep 1

echo "Starting Frontend on :3001 ..."
(cd ui/management/frontend && npm run dev -- --host) &
sleep 1

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  SkillScale UI:  http://localhost:3001       ║"
echo "║  API Server:     http://localhost:8401       ║"
echo "╠══════════════════════════════════════════════╣"
echo "║  Tabs: Dashboard | Chat Testing | Traces     ║"
echo "║  Press Ctrl+C to stop all services           ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

wait
