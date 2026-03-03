#!/usr/bin/env bash
#
# run_all.sh — Bootstrap and Launch SkillScale System
#
# This script:
#   1. Sets up the Python environment and installs dependencies.
#   2. Invokes build.sh to construct and launch the Docker services
#      (ZMQ proxy, Skill servers, internal agents).
#   3. Starts the Transparent Gateway (A2A / MCP) locally.
#
set -euo pipefail
cd "$(dirname "$0")"

echo "=========================================="
echo "    Launching SkillScale Core System      "
echo "=========================================="

# 1. Setup Python Virtual Environment
echo ""
echo "[1] Setting up Python dependencies..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt

# 2. Build and Start Docker Services
echo ""
echo "[2] Starting Docker core services via build.sh..."
bash build.sh

# Let ZMQ bus stabilize
sleep 3

echo ""
echo "[3] Starting Transparent Gateway (MCP & A2A Bridge)..."
echo "    - MCP Stdio Server & A2A Server bound to port 8081."
echo "    -> Press Ctrl+C to terminate gateway."
echo ""
python3 gateway/transparent_layer.py
