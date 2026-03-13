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
export PYTHONPATH="${PYTHONPATH:-}:."

# 2. Build and Start Docker Services
echo ""
echo "[2] Starting Docker core services via build.sh..."
# Purge Kafka/Redpanda volumes to avoid stale messages from previous runs
docker compose down -v 2>/dev/null || true
# Pass arguments like --no-clean to build.sh if provided
bash build.sh "$@"

# Wait for Rust Gateway (A2A on 8085, MCP on 8086)
echo "Waiting for Rust Gateway..."
for ((i=1; i<=30; i++)); do
    if curl -s http://localhost:8085/health >/dev/null 2>&1; then
        echo "  A2A port 8085 is ready."
        break
    fi
    echo -n "."
    sleep 2
done
for ((i=1; i<=15; i++)); do
    if python3 -c "import socket; s = socket.socket(); s.settimeout(1); s.connect(('localhost', 8086))" 2>/dev/null; then
        echo "  MCP port 8086 is ready."
        break
    fi
    sleep 1
done
echo ""

# 3. Validation
echo "[3] Validating SkillScale Gateway..."

# 3.1 Verify A2A Protocol (HTTP)
echo "- Validating A2A Client Demo (HTTP)..."
# We use the python script but ensure it points to localhost:8085
if python3 examples/demo_a2a_client.py; then
    echo "  ✓ A2A Client Demo Passed"
else
    echo "  ✗ A2A Client Demo Failed (Check docker logs for gateway)"
    exit 1
fi

echo ""
# 3.2 Verify MCP Protocol (Streamable HTTP)
echo "- Validating MCP Client Demo (http://localhost:8086/mcp)..."
if python3 examples/demo_mcp_client.py; then
    echo "  ✓ MCP Client Demo Passed"
else
    echo "  ✗ MCP Client Demo Failed"
    exit 1
fi

echo ""
echo "=========================================="
echo "    SkillScale System Ready!              "
echo "=========================================="
echo "  • A2A Gateway:    http://localhost:8085"
echo "  • MCP Server:     http://localhost:8086/mcp"
echo "  • Console (Web):  http://localhost:8080"
echo "  • Kafka Broker:   localhost:9092"
echo "=========================================="

