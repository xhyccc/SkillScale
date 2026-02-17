#!/bin/bash
# Launch all SkillScale services with nohup, then run the E2E test.
set -e

cd "$(dirname "$0")"

# Clean up old processes
pkill -9 -f skillscale_proxy 2>/dev/null || true
pkill -9 -f skillscale_skill_server 2>/dev/null || true
sleep 2

# Start proxy (nohup, fully detached)
nohup ./proxy/build/skillscale_proxy > /tmp/skillscale_proxy.log 2>&1 &
PROXY_PID=$!
echo "[launcher] Proxy started (pid=$PROXY_PID)"
sleep 1

# Start skill server (nohup, fully detached)
nohup ./skill-server/build/skillscale_skill_server \
    --topic TOPIC_DATA_PROCESSING \
    --description "Data processing server — text summarization, CSV analysis" \
    --skills-dir "$(pwd)/skills/data-processing" \
    > /tmp/skillscale_server.log 2>&1 &
SERVER_PID=$!
echo "[launcher] Skill server started (pid=$SERVER_PID)"
sleep 2

echo "[launcher] Proxy log:"
cat /tmp/skillscale_proxy.log
echo ""
echo "[launcher] Server log:"
cat /tmp/skillscale_server.log
echo ""

# Run E2E test
echo "[launcher] Running E2E test..."
./venv/bin/python3 test_e2e_live.py 2>&1 || ./.venv/bin/python3 test_e2e_live.py 2>&1
E2E_EXIT=$?

echo ""
echo "[launcher] E2E test exit code: $E2E_EXIT"

# Cleanup
kill $PROXY_PID 2>/dev/null || true
kill $SERVER_PID 2>/dev/null || true

exit $E2E_EXIT
