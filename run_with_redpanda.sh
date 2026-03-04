#!/usr/bin/env bash
#
# run_with_redpanda.sh — Bootstrap and Launch SkillScale System (Redpanda)
#
# This script:
#   1. Sets up the Python environment (installation of aiokafka).
#   2. Invokes build_with_redpanda.sh to construct and launch the Docker stack.
#
set -euo pipefail
cd "$(dirname "$0")"

echo "=========================================="
echo "    SkillScale: Enterprise (Redpanda)     "
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
echo "[2] Starting Redpanda services..."
bash build_with_redpanda.sh

echo ""
echo "Stack Running:"
echo "  - Broker:   localhost:9092"
echo "  - Console:  http://localhost:8080"
echo "  - Servers:  Python-based (Kafka Consumer) background workers"
echo ""
echo "To follow logs:"
echo "  docker compose -f docker-compose-redpanda.yml logs -f"
