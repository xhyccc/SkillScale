#!/usr/bin/env bash
#
# Launch SkillScale UI services
#
# Usage:
#   ./launch_ui.sh           # Launch both UIs
#   ./launch_ui.sh management # Launch management UI only
#   ./launch_ui.sh chat       # Launch chat UI only
#
set -euo pipefail
cd "$(dirname "$0")"

VENV_PYTHON="$(pwd)/.venv/bin/python3"
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Error: Python venv not found at $VENV_PYTHON"
    echo "Run: python3 -m venv .venv && .venv/bin/pip install -r ui/requirements.txt"
    exit 1
fi

MODE="${1:-all}"

cleanup() {
    echo ""
    echo "Shutting down UI services..."
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT

launch_management() {
    echo "Starting Management API on :8401 ..."
    "$VENV_PYTHON" -m uvicorn ui.management.server:app \
        --host 0.0.0.0 --port 8401 --log-level info &
    sleep 1

    echo "Starting Management Frontend on :3001 ..."
    (cd ui/management/frontend && npm run dev -- --host) &
    sleep 1
}

launch_chat() {
    echo "Starting Chat API on :8402 ..."
    "$VENV_PYTHON" -m uvicorn ui.chat.server:app \
        --host 0.0.0.0 --port 8402 --log-level info &
    sleep 1

    echo "Starting Chat Frontend on :3002 ..."
    (cd ui/chat/frontend && npm run dev -- --host) &
    sleep 1
}

case "$MODE" in
    management)
        launch_management
        ;;
    chat)
        launch_chat
        ;;
    all)
        launch_management
        launch_chat
        ;;
    *)
        echo "Usage: $0 [management|chat|all]"
        exit 1
        ;;
esac

echo ""
echo "╔══════════════════════════════════════════════╗"
if [[ "$MODE" == "all" || "$MODE" == "management" ]]; then
echo "║  Management UI:  http://localhost:3001       ║"
echo "║  Management API: http://localhost:8401       ║"
fi
if [[ "$MODE" == "all" || "$MODE" == "chat" ]]; then
echo "║  Chat UI:        http://localhost:3002       ║"
echo "║  Chat API:       http://localhost:8402       ║"
fi
echo "╠══════════════════════════════════════════════╣"
echo "║  Press Ctrl+C to stop all services           ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

wait
