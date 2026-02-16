#!/bin/bash
# SkillScale — Local development bootstrap
#
# Installs all dependencies and builds C++ components.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════"
echo "  SkillScale — Local Dev Setup"
echo "═══════════════════════════════════════════"

# ── Check prerequisites ──
check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo "❌ $1 not found. Please install it."
        return 1
    fi
    echo "✅ $1 found"
}

echo ""
echo "Checking prerequisites..."
check_cmd cmake
check_cmd python3
check_cmd pip3 || check_cmd pip

# ── Install Python dependencies ──
echo ""
echo "Installing Python dependencies..."
pip3 install --quiet -r agent/requirements.txt
pip3 install --quiet -r tests/requirements.txt

# ── Check for ZeroMQ C library ──
echo ""
echo "Checking for libzmq..."
if pkg-config --exists libzmq 2>/dev/null; then
    echo "✅ libzmq found ($(pkg-config --modversion libzmq))"
else
    echo "⚠️  libzmq not found via pkg-config."
    echo "   Install with: brew install zeromq cppzmq nlohmann-json"
    echo "   Or: apt-get install libzmq3-dev"
fi

# ── Build C++ Proxy ──
echo ""
echo "Building C++ Proxy..."
mkdir -p proxy/build
cd proxy/build
if cmake .. 2>/dev/null && make -j$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4) 2>/dev/null; then
    echo "✅ Proxy built successfully"
else
    echo "⚠️  Proxy build failed (missing C++ deps?). Python tests still work."
fi
cd "$SCRIPT_DIR"

# ── Build C++ Skill Server ──
echo ""
echo "Building C++ Skill Server..."
mkdir -p skill-server/build
cd skill-server/build
if cmake .. 2>/dev/null && make -j$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4) 2>/dev/null; then
    echo "✅ Skill Server built successfully"
else
    echo "⚠️  Skill Server build failed (missing C++ deps?). Python tests still work."
fi
cd "$SCRIPT_DIR"

echo ""
echo "═══════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  Run tests:     python3 -m pytest tests/ -v"
echo "  Run agent:     python3 agent/main.py"
echo "  Docker:        docker compose up --build"
echo "═══════════════════════════════════════════"
