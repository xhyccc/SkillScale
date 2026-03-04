#!/usr/bin/env bash
#
# run_all_with_redpanda.sh — Bootstrap and Launch SkillScale System (Redpanda + C++ Worker)
#
# This script:
#   1. Sets up the Python environment (installation of aiokafka, etc.)
#   2. Builds the C++ Skill Server locally (via build_with_redpanda.sh --build-only).
#   3. Launches Redpanda (Docker) + Console.
#   4. Launches Python Gateway (locally) in A2A Mode.
#   5. Launches C++ Skill Server (locally).
#   6. Runs Validation Suites (MCP + A2A Demos).
#
set -euo pipefail

# Check for macOS or Linux for specific utilities
if [[ "$OSTYPE" == "darwin"* ]]; then
    SLEEP_CMD="sleep 1"
else
    SLEEP_CMD="sleep 1" 
fi
cd "$(dirname "$0")"

# Trap to ensure cleanup on exit
trap cleanup EXIT INT TERM

cleanup() {
    echo ""
    echo "[!] Stopping background processes..."
    # Kill all child processes of this script
    pkill -P $$ || true
    
    echo "[!] Stopping Redpanda containers..."
    if [ -f "docker-compose-redpanda.yml" ]; then
        docker compose -f docker-compose-redpanda.yml stop 2>/dev/null || true
    fi
}

echo "=========================================="
echo "    SkillScale: Enterprise (Redpanda + C++)"
echo "=========================================="

# 1. Setup Python Virtual Environment
echo ""
echo "[1] Setting up Python dependencies..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# Install core deps + kafka support
pip install -r requirements.txt > /dev/null
pip install aiokafka > /dev/null

# 2. Build Components
echo ""
echo "[2] Building Local C++ Server & Docker Images..."
# Pass flags (like --no-clean) to build script, force --build-only
bash build_with_redpanda.sh "$@" --build-only

# Check for C++ binary
BINARY="./build/skill-server/skillscale_skill_server"
if [ ! -f "$BINARY" ]; then
    echo "Error: C++ binary not found at $BINARY"
    exit 1
fi

# 3. Launch Redpanda Infrastructure
echo ""
echo "[3] Launching Redpanda Broker..."
if [ -f "docker-compose-redpanda.yml" ]; then
    docker compose -f docker-compose-redpanda.yml up -d redpanda
else
    echo "Error: docker-compose-redpanda.yml not found (build failed?)"
    exit 1
fi

echo "Waiting for Redpanda (localhost:19092)..."
for ((i=1; i<=30; i++)); do
    if python3 -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('localhost', 19092))" 2>/dev/null; then
        echo "Redpanda Broker is ready!"
        break
    fi
    sleep 1
done

# 4. Launch Services
echo ""
echo "[4] Launching Services..."

# Python Gateway (Transparent Layer)
if [ -f "gateway/transparent_layer.py" ]; then
    echo "-> Starting Gateway (transparent_layer.py)..."
    # Set backend to kafka so it uses SkillScaleKafkaClient
    # Disable MCP Stdio mode (as it's running as a daemon)
    export SKILLSCALE_BACKEND=kafka
    export SKILLSCALE_PROTOCOL_A2A=1
    export SKILLSCALE_PROTOCOL_MCP=0
    python3 gateway/transparent_layer.py &
else
    echo "Warning: gateway/transparent_layer.py not found, skipping."
fi

# C++ Skill Server (Worker)
# Assuming typical Redpanda broker locally is localhost:19092
echo "-> Starting C++ Skill Server (Worker)..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS typical binding
    $BINARY --backend redpanda --brokers localhost:19092 &
else
    # Linux (and some Docker bridge setups) might need host.docker.internal or just localhost
    # But usually localhost:19092 is exposed via Docker port mapping anyway.
    $BINARY --backend redpanda --brokers localhost:19092 &
fi

echo "Waiting for Gateway (A2A Port 8085)..."
for ((i=1; i<=15; i++)); do
    if python3 -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('localhost', 8085))" 2>/dev/null; then
        echo "Authentication / A2A Gateway is ready on port 8085!"
        break
    fi
    sleep 1
done

# 5. Running Validation Suites
echo ""
echo "[5] Running Validation Suites..."

echo "-> Testing MCP Client (Protocol: Stdio -> Redpanda)..."
# Since demo_mcp_client.py spawns its own gateway instance, we pass env vars to ensure backend=kafka
env SKILLSCALE_BACKEND=kafka python3 gateway/demo_mcp_client.py || { echo "[FAIL] MCP Client Validation Failed"; exit 1; }
echo "[OK] MCP Client Validation Passed"

echo "-> Testing Google A2A Client (Protocol: HTTP -> Gateway:8085 -> Redpanda)..."
python3 gateway/demo_a2a_client.py || { echo "[FAIL] A2A Client Validation Failed"; exit 1; }
echo "[OK] A2A Client Validation Passed"

echo ""
echo "System Running!"
echo "Redpanda is at localhost:19092"
echo "Gateway A2A is at localhost:8085"
echo "Press Ctrl+C to stop."

# Wait for any process to exit
wait
