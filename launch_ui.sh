#!/usr/bin/env bash
#
# Launch SkillScale UI (+ backend services if needed).
#
# Auto-detects Docker:
#   If Docker containers are running (via build.sh), only starts the UI:
#     • FastAPI backend (port 8401)
#     • Vite React frontend (port 3001)
#
#   If no Docker containers found, starts everything locally:
#     • C++ Proxy, Skill Servers, FastAPI, Vite frontend
#
# Usage:
#   bash launch_ui.sh              # auto-detect mode
#   bash launch_ui.sh --docker     # force Docker mode (UI only)
#   bash launch_ui.sh --local      # force local mode (all services)
#
# Environment:
#   MATCHER=keyword|llm   Skill matching strategy (default: llm)
#
set -euo pipefail
cd "$(dirname "$0")"

MATCHER="${MATCHER:-llm}"
ROOT="$(pwd)"

# ── Parse flags ──
FORCE_MODE=""
for arg in "$@"; do
    case "$arg" in
        --docker) FORCE_MODE="docker" ;;
        --local)  FORCE_MODE="local" ;;
        --help|-h)
            echo "Usage: bash launch_ui.sh [--docker|--local]"
            echo "  --docker  Force Docker mode (only start UI, expect Docker backend)"
            echo "  --local   Force local mode (start proxy + skill servers + UI)"
            echo "  (default) Auto-detect Docker containers"
            exit 0
            ;;
    esac
done

# ── Load .env if present ──
if [[ -f "$ROOT/.env" ]]; then
    set -a
    source "$ROOT/.env" 2>/dev/null || true
    set +a
fi

SKILLSCALE_TIMEOUT="${SKILLSCALE_TIMEOUT:-180000}"

# ── Detect Python venv ──
VENV_PYTHON="$ROOT/.venv/bin/python3"
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Error: Python venv not found at $VENV_PYTHON"
    echo "Run ./setup.sh first."
    exit 1
fi

# ── Auto-detect mode ──
DOCKER_MODE=false
if [[ "$FORCE_MODE" == "docker" ]]; then
    DOCKER_MODE=true
elif [[ "$FORCE_MODE" == "local" ]]; then
    DOCKER_MODE=false
else
    # Auto-detect: check if Docker proxy container is running and healthy
    if docker compose ps --format json 2>/dev/null | grep -q '"proxy"' 2>/dev/null; then
        DOCKER_MODE=true
        echo "✓ Detected Docker containers running (via build.sh)"
        echo "  Starting UI only — backend handled by Docker."
    elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q "skillscale.*proxy"; then
        DOCKER_MODE=true
        echo "✓ Detected Docker containers running"
        echo "  Starting UI only — backend handled by Docker."
    fi
fi

# ── Cleanup on Ctrl+C / exit ──
cleanup() {
    echo ""
    echo "Shutting down UI services..."
    kill $(jobs -p) 2>/dev/null || true
    if ! $DOCKER_MODE; then
        pkill -f skillscale_proxy 2>/dev/null || true
        pkill -f skillscale_skill_server 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT

# ── Start backend services (local mode only) ──
if ! $DOCKER_MODE; then
    echo ""
    echo "── Local mode: starting proxy + skill servers ──"

    # Check that C++ binaries exist
    if [[ ! -x "$ROOT/proxy/build/skillscale_proxy" ]]; then
        echo "Error: proxy binary not found at proxy/build/skillscale_proxy"
        echo "Build it first: cd proxy && mkdir -p build && cd build && cmake .. && make"
        echo "Or use 'bash build.sh' to build Docker containers instead."
        exit 1
    fi
    if [[ ! -x "$ROOT/skill-server/build/skillscale_skill_server" ]]; then
        echo "Error: skill-server binary not found at skill-server/build/skillscale_skill_server"
        echo "Build it first: cd skill-server && mkdir -p build && cd build && cmake .. && make"
        echo "Or use 'bash build.sh' to build Docker containers instead."
        exit 1
    fi

    # Kill stale local processes
    pkill -9 -f skillscale_proxy 2>/dev/null || true
    pkill -9 -f skillscale_skill_server 2>/dev/null || true
    sleep 1

    # 1. C++ Proxy
    echo "Starting C++ Proxy..."
    "$ROOT/proxy/build/skillscale_proxy" > /tmp/skillscale_proxy.log 2>&1 &
    sleep 1

    # 2. Skill Servers — scan skills/ for servers
    for dir in "$ROOT"/skills/*/; do
        dirname=$(basename "$dir")
        [[ "$dirname" == "__pycache__" ]] && continue
        [[ ! -f "$dir/AGENTS.md" ]] && continue

        topic="TOPIC_$(echo "$dirname" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
        echo "Starting Skill Server ($dirname, matcher=$MATCHER)..."
        "$ROOT/skill-server/build/skillscale_skill_server" \
            --topic "$topic" \
            --description "$dirname skill server" \
            --skills-dir "$dir" \
            --matcher "$MATCHER" \
            --python "$VENV_PYTHON" \
            --skill-exec-timeout "$SKILLSCALE_TIMEOUT" \
            > "/tmp/skillscale_server_${dirname}.log" 2>&1 &
    done
    sleep 2
else
    echo ""
    echo "── Docker mode: proxy + skill servers running in containers ──"
fi

# ── FastAPI backend ──
echo "Starting SkillScale API on :8401..."
"$VENV_PYTHON" -m uvicorn ui.management.server:app \
    --host 0.0.0.0 --port 8401 --log-level info > /tmp/skillscale_api.log 2>&1 &
sleep 1

# ── Vite frontend ──
echo "Starting Frontend on :3001..."
(cd ui/management/frontend && npm run dev -- --host) > /tmp/skillscale_frontend.log 2>&1 &
sleep 2

# ── Open browser ──
URL="http://localhost:3001"
if command -v open &>/dev/null; then
    open "$URL"
elif command -v xdg-open &>/dev/null; then
    xdg-open "$URL"
elif command -v wslview &>/dev/null; then
    wslview "$URL"
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  SkillScale UI:  http://localhost:3001           ║"
echo "║  API Server:     http://localhost:8401           ║"
echo "╠══════════════════════════════════════════════════╣"
if $DOCKER_MODE; then
echo "║  Mode: Docker (proxy + skills in containers)     ║"
echo "║  Services:                                       ║"
echo "║    • FastAPI backend (:8401)                      ║"
echo "║    • React frontend (:3001)                       ║"
echo "║    • Docker: proxy + skill servers                ║"
else
echo "║  Mode: Local (all services native)                ║"
echo "║  Services:                                       ║"
echo "║    • C++ Proxy (XPUB/XSUB)                       ║"
echo "║    • Skill Servers (auto-discovered)              ║"
echo "║    • FastAPI backend (:8401)                      ║"
echo "║    • React frontend (:3001)                       ║"
echo "║  Matcher: $MATCHER"
fi
echo "║                                                  ║"
echo "║  Logs: /tmp/skillscale_*.log                      ║"
echo "║  Press Ctrl+C to stop                            ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

wait
