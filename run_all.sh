#!/usr/bin/env bash
#
# run_all.sh — Bootstrap and Launch SkillScale System (Rust + Redpanda)
#
# This script:
#   1. Sets up the Python environment (installation of A2A protocol, etc.)
#   2. Invokes build.sh to construct and launch the Docker services
#      (Rust Gateway, Skill Servers, Redpanda).
#   3. Waits for services to be ready.
#   4. Validate endpoints (A2A).
#
set -euo pipefail
cd "$(dirname "$0")"

echo "=========================================="
echo "    Launching SkillScale Core System      "
echo "    Architecture: Rust + Redpanda (Kafka) "
echo "=========================================="

# 1. Setup Python Virtual Environment
echo ""
echo "[1] Setting up Python dependencies..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt >/dev/null
export PYTHONPATH=$PYTHONPATH:.

# 2. Build and Start Docker Services
echo ""
echo "[2] Starting Docker core services via build.sh..."
# Pass arguments like --no-clean to build.sh if provided
bash build.sh "$@"

# Wait for Rust Gateway (Port 8085)
echo "Waiting for Rust Gateway (localhost:8085)..."
for ((i=1; i<=30; i++)); do
    if curl -s http://localhost:8085 >/dev/null; then
        echo "Rust Gateway is responding!"
        break
    fi
     # Also try socket check if endpoint returns 404/500
    if python3 -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('localhost', 8085))" 2>/dev/null; then
        echo "Rust Gateway port is open!"
        break
    fi
    echo -n "."
    sleep 2
done
echo ""

# 3. Validation
echo "[3] Validating SkillScale Gateway..."

# 3.1 Verify A2A Protocol (HTTP)
echo "- Validating A2A Client Demo (HTTP)..."
# We use the python script but ensure it points to localhost:8085
if python3 gateway/demo_a2a_client.py; then
    echo "  ✓ A2A Client Demo Passed"
else
    echo "  ✗ A2A Client Demo Failed (Check docker logs for gateway)"
    exit 1
fi

echo ""
echo "=========================================="
echo "    SkillScale System Ready!              "
echo "=========================================="
echo "  • Gateway (HTTP): http://localhost:8085"
echo "  • Console (Web):  http://localhost:8080"
echo "  • Kafka Broker:   localhost:9092"
echo "=========================================="

else
    echo "  ✗ MCP Client Test Failed"
    exit 1
fi

# 3.2 Start Gateway (Background) & Verify A2A
echo "- Starting Gateway for A2A Protocol..."
export PYTHONPATH=$PYTHONPATH:.
# Start Gateway in background, logging to file
SKILLSCALE_PROTOCOL_MCP=0 python3 gateway/transparent_layer.py > gateway.log 2>&1 &
GATEWAY_PID=$!
echo "  PID: $GATEWAY_PID"

# Trap to ensure we kill the gateway if the script exits abnormally (during startup/tests)
# Once we reach the end successfully, we want the process to stay alive, so we'll clear the trap.
trap "kill $GATEWAY_PID 2>/dev/null" EXIT

# Wait for A2A Port 8085
echo "  Waiting for A2A Server (port 8085)..."
for ((i=1; i<=15; i++)); do
    if python3 -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('localhost', 8085))" 2>/dev/null; then
        echo "  Port 8085 is ready."
        break
    fi
    sleep 1
done

echo "- Validating A2A Connectivity..."
if python3 gateway/demo_a2a_client.py; then
    echo "  ✓ A2A Client Test Passed"
else
    echo "  ✗ A2A Client Test Failed (see output above / gateway.log)"
    tail -n 20 gateway.log
    exit 1
fi

# Clear the trap so the gateway stays running after script exit
trap - EXIT

echo ""
echo "=========================================="
echo "    All Systems GO!                       "
echo "=========================================="
echo "Gateway A2A Server running on port 8085."
echo "PID: $GATEWAY_PID"
echo "Log: gateway.log"
echo ""
echo "Run 'tail -f gateway.log' to follow logs."
echo "Run 'kill $GATEWAY_PID' to stop the gateway."
echo ""


