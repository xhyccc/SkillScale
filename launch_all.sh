#!/bin/bash
# Launch all SkillScale services with nohup, then run the E2E test.
#
# Architecture:
#   C++ Proxy (XPUB/XSUB)     ← ZMQ middleware
#   C++ Skill Server × 2      ← OpenSkills invocation (AGENTS.md + CLI)
#   Python Agent               ← LLM-powered intent routing
#
set -e

cd "$(dirname "$0")"

# Clean up old processes
pkill -9 -f skillscale_proxy 2>/dev/null || true
pkill -9 -f "skill-server-py/server.py" 2>/dev/null || true
pkill -9 -f skillscale_skill_server 2>/dev/null || true
sleep 2

# Detect Python venv
PYTHON="python3"
[[ -x ./venv/bin/python3 ]] && PYTHON="./venv/bin/python3"
[[ -x ./.venv/bin/python3 ]] && PYTHON="./.venv/bin/python3"

# Start C++ proxy (ZMQ middleware — unchanged)
nohup ./proxy/build/skillscale_proxy > /tmp/skillscale_proxy.log 2>&1 &
PROXY_PID=$!
echo "[launcher] Proxy started (pid=$PROXY_PID)"
sleep 1

# Start C++ skill server: data-processing (text-summarizer + csv-analyzer)
nohup ./skill-server/build/skillscale_skill_server \
    --topic TOPIC_DATA_PROCESSING \
    --description "Data processing server — text summarization, CSV analysis, and general data transformation" \
    --skills-dir "$(pwd)/skills/data-processing" \
    > /tmp/skillscale_server_data.log 2>&1 &
SERVER_DATA_PID=$!
echo "[launcher] Skill server (data-processing) started (pid=$SERVER_DATA_PID)"

# Start C++ skill server: code-analysis (code-complexity + dead-code-detector)
nohup ./skill-server/build/skillscale_skill_server \
    --topic TOPIC_CODE_ANALYSIS \
    --description "Code analysis server — cyclomatic complexity metrics, dead code detection, and Python static analysis" \
    --skills-dir "$(pwd)/skills/code-analysis" \
    > /tmp/skillscale_server_code.log 2>&1 &
SERVER_CODE_PID=$!
echo "[launcher] Skill server (code-analysis) started (pid=$SERVER_CODE_PID)"
sleep 2

echo "[launcher] Proxy log:"
cat /tmp/skillscale_proxy.log
echo ""
echo "[launcher] Data-processing server log:"
cat /tmp/skillscale_server_data.log
echo ""
echo "[launcher] Code-analysis server log:"
cat /tmp/skillscale_server_code.log
echo ""

# Run E2E test
echo "[launcher] Running E2E test..."
$PYTHON test_e2e_live.py 2>&1
E2E_EXIT=$?

echo ""
echo "[launcher] E2E test exit code: $E2E_EXIT"

# Cleanup
kill $PROXY_PID 2>/dev/null || true
kill $SERVER_DATA_PID 2>/dev/null || true
kill $SERVER_CODE_PID 2>/dev/null || true

exit $E2E_EXIT
