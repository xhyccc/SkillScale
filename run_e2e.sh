#!/bin/bash
# SkillScale — Full E2E test with real C++ proxy + C++ skill server + Python agent
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

cleanup() {
    echo "[runner] Cleaning up..."
    kill $PROXY_PID 2>/dev/null || true
    kill $SERVER_PID 2>/dev/null || true
    wait $PROXY_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
    echo "[runner] Done."
}
trap cleanup EXIT

# Kill any stale processes
pkill -9 -f "skillscale_proxy" 2>/dev/null || true
pkill -9 -f "skillscale_skill_server" 2>/dev/null || true
sleep 1

echo "═══════════════════════════════════════════"
echo "  SkillScale — Full Stack E2E Test"
echo "═══════════════════════════════════════════"

# Start C++ proxy
echo "[runner] Starting C++ proxy..."
./proxy/build/skillscale_proxy &
PROXY_PID=$!
sleep 1

# Start C++ skill server
echo "[runner] Starting C++ skill server (TOPIC_DATA_PROCESSING)..."
./skill-server/build/skillscale_skill_server \
    --topic TOPIC_DATA_PROCESSING \
    --skills-dir ./skills/data-processing &
SERVER_PID=$!
sleep 2

# Run the Python E2E test
echo "[runner] Running Python E2E test..."
source .venv/bin/activate
python3 test_e2e_live.py
EXIT_CODE=$?

echo ""
echo "[runner] E2E test exited with code: $EXIT_CODE"
exit $EXIT_CODE
