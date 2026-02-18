#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# build.sh — Build & launch SkillScale from source + .env
#
# This script:
#   1. Reads all configuration from .env
#   2. Cleans compiled artifacts (C++ build dirs, __pycache__, etc.)
#   3. Generates opencode.json from .env values
#   4. Generates docker-compose.yml by scanning skills/ subfolders
#   5. Builds Docker images for proxy, each skill server, and agent
#   6. Launches the full Docker stack
#
# Usage:
#   bash build.sh              # full clean + build + launch
#   bash build.sh --no-clean   # build + launch (skip cleaning)
#   bash build.sh --build-only # clean + build images only
#   bash build.sh --down       # stop all containers
# ════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[build]${NC} $*"; }
warn() { echo -e "${YELLOW}[build]${NC} $*"; }
err()  { echo -e "${RED}[build]${NC} $*" >&2; }
step() { echo -e "\n${CYAN}${BOLD}══ $* ══${NC}"; }

# ── Parse flags ──
NO_CLEAN=false
BUILD_ONLY=false
DOWN_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --no-clean)   NO_CLEAN=true ;;
        --build-only) BUILD_ONLY=true ;;
        --down)       DOWN_ONLY=true ;;
        --help|-h)
            echo "Usage: bash build.sh [OPTIONS]"
            echo "  --no-clean    Skip cleaning compiled artifacts"
            echo "  --build-only  Build images only, don't launch"
            echo "  --down        Stop all containers and exit"
            echo "  -h, --help    Show this help"
            exit 0
            ;;
        *) err "Unknown option: $arg"; exit 1 ;;
    esac
done

# ── Handle --down ──
if $DOWN_ONLY; then
    step "Stopping all SkillScale containers"
    docker compose down 2>/dev/null || true
    log "All containers stopped."
    exit 0
fi

# ════════════════════════════════════════════════════════════
# 1. Load .env
# ════════════════════════════════════════════════════════════
step "Loading configuration from .env"

if [[ ! -f .env ]]; then
    err ".env file not found! Create one from .env.example or configure manually."
    exit 1
fi

set -a
source .env
set +a

# Validate required keys
REQUIRED_VARS=(OPENAI_API_KEY OPENAI_API_BASE OPENAI_MODEL SKILLSCALE_TIMEOUT PUBLISH_TIMEOUT LLM_PROVIDER)
for var in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        err "Required variable $var is not set in .env"
        exit 1
    fi
done

log "LLM_PROVIDER=$LLM_PROVIDER"
log "OPENAI_API_BASE=$OPENAI_API_BASE"
log "OPENAI_MODEL=$OPENAI_MODEL"
log "SKILLSCALE_TIMEOUT=${SKILLSCALE_TIMEOUT}ms"
log "PUBLISH_TIMEOUT=${PUBLISH_TIMEOUT}s"

# ════════════════════════════════════════════════════════════
# 2. Clean compiled artifacts
# ════════════════════════════════════════════════════════════
if ! $NO_CLEAN; then
    step "Cleaning compiled artifacts"

    # C++ build directories
    for dir in skill-server/build proxy/build; do
        if [[ -d "$dir" ]]; then
            log "Removing $dir/"
            rm -rf "$dir"
        fi
    done

    # Python caches
    find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -not -path "./.venv/*" -delete 2>/dev/null || true
    rm -rf skillscale.egg-info/ 2>/dev/null || true

    # Docker — remove old images (optional, keeps cache)
    log "Pruning dangling Docker images"
    docker image prune -f 2>/dev/null || true

    log "Clean complete."
else
    log "Skipping clean (--no-clean)."
fi

# ════════════════════════════════════════════════════════════
# 3. Generate opencode.json from .env
# ════════════════════════════════════════════════════════════
step "Generating opencode.json from .env"

# Extract provider name from OPENAI_API_BASE for labeling
PROVIDER_LABEL="Custom Provider"
if [[ "$OPENAI_API_BASE" == *"siliconflow"* ]]; then
    PROVIDER_LABEL="SiliconFlow"
elif [[ "$OPENAI_API_BASE" == *"openai.com"* ]]; then
    PROVIDER_LABEL="OpenAI"
elif [[ "$OPENAI_API_BASE" == *"cognitiveservices"* ]]; then
    PROVIDER_LABEL="Azure OpenAI"
fi

# Derive a short provider id (lowercase, no special chars)
PROVIDER_ID=$(echo "$PROVIDER_LABEL" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')

# Derive model alias from OPENAI_MODEL (last segment)
MODEL_ALIAS=$(echo "$OPENAI_MODEL" | tr '/' '-' | tr '[:upper:]' '[:lower:]')

cat > opencode.json <<OPENCODE_JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "${PROVIDER_ID}": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "${PROVIDER_LABEL} (${OPENAI_MODEL##*/})",
      "options": {
        "baseURL": "${OPENAI_API_BASE}"
      },
      "models": {
        "${MODEL_ALIAS}": {
          "id": "${OPENAI_MODEL}",
          "name": "${OPENAI_MODEL##*/}",
          "limit": {
            "context": 64000,
            "output": 8192
          }
        }
      }
    }
  },
  "model": "${PROVIDER_ID}/${MODEL_ALIAS}",
  "autoupdate": false,
  "share": "disabled",
  "tools": {
    "write": false,
    "edit": false
  },
  "permission": {
    "bash": "allow",
    "read": "allow"
  }
}
OPENCODE_JSON

log "Generated opencode.json (provider=${PROVIDER_ID}, model=${OPENAI_MODEL})"

# ════════════════════════════════════════════════════════════
# 4. Scan skills/ and generate docker-compose.yml
# ════════════════════════════════════════════════════════════
step "Scanning skills/ and generating docker-compose.yml"

# Discover skill server directories (each subfolder under skills/ with an AGENTS.md)
SKILL_DIRS=()
SKILL_NAMES=()
SKILL_TOPICS=()
SKILL_DESCS=()

for dir in skills/*/; do
    # Skip __pycache__ and non-skill dirs
    dirname=$(basename "$dir")
    [[ "$dirname" == "__pycache__" ]] && continue
    [[ ! -f "$dir/AGENTS.md" ]] && continue

    SKILL_DIRS+=("$dir")
    SKILL_NAMES+=("$dirname")

    # Derive docker topic name: data-processing → TOPIC_DATA_PROCESSING
    topic="TOPIC_$(echo "$dirname" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
    SKILL_TOPICS+=("$topic")

    # Extract first line of AGENTS.md as description (strip markdown heading)
    desc=$(head -1 "$dir/AGENTS.md" | sed 's/^#* *//')
    SKILL_DESCS+=("$desc")

    log "Found skill server: ${CYAN}$dirname${NC} → topic=$topic"
done

if [[ ${#SKILL_DIRS[@]} -eq 0 ]]; then
    err "No skill server directories found under skills/!"
    err "Each skill server needs a subfolder with an AGENTS.md file."
    exit 1
fi

log "Discovered ${#SKILL_DIRS[@]} skill server(s)."

# ── Generate docker-compose.yml ──
COMPOSE_FILE="docker-compose.yml"

# Build the env block shared by all skill servers
ENV_BLOCK='      SKILLSCALE_SKILLS_DIR: /skills
      SKILLSCALE_PROXY_XPUB: tcp://proxy:5555
      SKILLSCALE_PROXY_XSUB: tcp://proxy:5444
      SKILLSCALE_WORKERS: "2"
      SKILLSCALE_TIMEOUT: "'"${SKILLSCALE_TIMEOUT}"'"
      SILICONFLOW_API_KEY: "'"${OPENAI_API_KEY}"'"
      LLM_PROVIDER: "'"${LLM_PROVIDER}"'"
      OPENAI_API_KEY: "'"${OPENAI_API_KEY}"'"
      OPENAI_API_BASE: "'"${OPENAI_API_BASE}"'"
      OPENAI_MODEL: "'"${OPENAI_MODEL}"'"'

# Add optional provider keys if set
[[ -n "${AZURE_API_KEY:-}" ]]     && ENV_BLOCK+=$'\n''      AZURE_API_KEY: "'"${AZURE_API_KEY}"'"'
[[ -n "${AZURE_API_BASE:-}" ]]    && ENV_BLOCK+=$'\n''      AZURE_API_BASE: "'"${AZURE_API_BASE}"'"'
[[ -n "${AZURE_MODEL:-}" ]]       && ENV_BLOCK+=$'\n''      AZURE_MODEL: "'"${AZURE_MODEL}"'"'
[[ -n "${AZURE_API_VERSION:-}" ]] && ENV_BLOCK+=$'\n''      AZURE_API_VERSION: "'"${AZURE_API_VERSION}"'"'
[[ -n "${ZHIPU_API_KEY:-}" ]]     && ENV_BLOCK+=$'\n''      ZHIPU_API_KEY: "'"${ZHIPU_API_KEY}"'"'
[[ -n "${ZHIPU_MODEL:-}" ]]       && ENV_BLOCK+=$'\n''      ZHIPU_MODEL: "'"${ZHIPU_MODEL}"'"'

# Start composing
cat > "$COMPOSE_FILE" <<'HEADER'
# SkillScale — Auto-generated Docker Compose
# Generated by build.sh — DO NOT EDIT MANUALLY
#
# Topology:
#   agent ──PUB──▶ proxy (XSUB:5444) ──▶ proxy (XPUB:5555) ──SUB──▶ skill-server-*
#   agent ◀──SUB── proxy (XPUB:5555) ◀──PUB── skill-server-*
#

services:
  # ── Central XPUB/XSUB Proxy ──
  proxy:
    build:
      context: .
      dockerfile: docker/Dockerfile.proxy
    ports:
      - "5444:5444"   # XSUB — publishers connect here
      - "5555:5555"   # XPUB — subscribers connect here
      - "9100:9100"   # Prometheus metrics
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "echo | nc -z localhost 5444"]
      interval: 5s
      timeout: 3s
      retries: 5

HEADER

# ── Generate a service block for each skill server ──
SKILL_SERVICE_NAMES=()
for i in "${!SKILL_DIRS[@]}"; do
    name="${SKILL_NAMES[$i]}"
    topic="${SKILL_TOPICS[$i]}"
    desc="${SKILL_DESCS[$i]}"
    dir="${SKILL_DIRS[$i]}"
    svc_name="skill-server-${name}"
    SKILL_SERVICE_NAMES+=("$svc_name")

    cat >> "$COMPOSE_FILE" <<SKILL_SVC
  # ── Skill Server: ${name} ──
  ${svc_name}:
    build:
      context: .
      dockerfile: docker/Dockerfile.skill-server
    environment:
      SKILLSCALE_TOPIC: ${topic}
      SKILLSCALE_DESCRIPTION: "${desc}"
${ENV_BLOCK}
    volumes:
      - ./${dir}:/skills:ro
      - ./skills/llm_utils.py:/app/skills/llm_utils.py:ro
      - ./.claude/skills:/app/.claude/skills:ro
    depends_on:
      proxy:
        condition: service_healthy
    restart: unless-stopped

SKILL_SVC

    log "Generated service: ${svc_name} (${topic})"
done

# ── Generate agent service with depends_on for all skill servers ──
DEPENDS_BLOCK=""
for svc in "${SKILL_SERVICE_NAMES[@]}"; do
    DEPENDS_BLOCK+="      ${svc}:"$'\n'
    DEPENDS_BLOCK+="        condition: service_started"$'\n'
done

cat >> "$COMPOSE_FILE" <<AGENT_SVC
  # ── Python Front-End Agent ──
  agent:
    build:
      context: .
      dockerfile: docker/Dockerfile.agent
    environment:
      SKILLSCALE_PROXY_XSUB: tcp://proxy:5444
      SKILLSCALE_PROXY_XPUB: tcp://proxy:5555
      SKILLSCALE_TIMEOUT: "${PUBLISH_TIMEOUT}"
    depends_on:
      proxy:
        condition: service_healthy
${DEPENDS_BLOCK}    stdin_open: true
    tty: true
    restart: unless-stopped
AGENT_SVC

log "Generated docker-compose.yml with ${#SKILL_DIRS[@]} skill server(s) + proxy + agent."

# ════════════════════════════════════════════════════════════
# 5. Ensure Docker is running
# ════════════════════════════════════════════════════════════
step "Checking Docker"

if ! docker info &>/dev/null; then
    warn "Docker daemon not running. Attempting to start Docker Desktop..."
    open -a Docker 2>/dev/null || true
    for i in {1..30}; do
        docker info &>/dev/null && break
        sleep 2
    done
    if ! docker info &>/dev/null; then
        err "Docker daemon failed to start. Please start Docker Desktop manually."
        exit 1
    fi
fi
log "Docker is running."

# ════════════════════════════════════════════════════════════
# 6. Stop existing containers
# ════════════════════════════════════════════════════════════
step "Stopping existing containers"
docker compose down --remove-orphans 2>/dev/null || true
log "Old containers stopped."

# ════════════════════════════════════════════════════════════
# 7. Build all Docker images
# ════════════════════════════════════════════════════════════
step "Building Docker images"

log "Building proxy..."
docker compose build proxy 2>&1 | tail -3

for svc in "${SKILL_SERVICE_NAMES[@]}"; do
    log "Building ${svc}..."
    docker compose build "$svc" 2>&1 | tail -3
done

log "Building agent..."
docker compose build agent 2>&1 | tail -3

log "All images built successfully."

# ════════════════════════════════════════════════════════════
# 8. Launch (unless --build-only)
# ════════════════════════════════════════════════════════════
if $BUILD_ONLY; then
    step "Build complete (--build-only)"
    log "Run 'docker compose up -d' to start services."
    exit 0
fi

step "Launching SkillScale"
docker compose up -d 2>&1

# Wait for services to settle
sleep 3

# ════════════════════════════════════════════════════════════
# 9. Status report
# ════════════════════════════════════════════════════════════
step "Service Status"
docker compose ps

echo ""
log "${GREEN}${BOLD}SkillScale is running!${NC}"
echo ""
echo -e "  ${CYAN}Proxy XSUB:${NC}  tcp://localhost:5444  (publishers connect here)"
echo -e "  ${CYAN}Proxy XPUB:${NC}  tcp://localhost:5555  (subscribers connect here)"
echo -e "  ${CYAN}Metrics:${NC}     http://localhost:9100"
echo ""
echo -e "  ${CYAN}Skill servers:${NC}"
for i in "${!SKILL_NAMES[@]}"; do
    echo -e "    • ${BOLD}${SKILL_NAMES[$i]}${NC}  →  ${SKILL_TOPICS[$i]}"
done
echo ""
echo -e "  ${CYAN}Commands:${NC}"
echo "    docker compose logs -f          # follow all logs"
echo "    docker compose logs <service>   # single service logs"
echo "    docker compose down             # stop everything"
echo "    bash build.sh --down            # stop everything"
echo ""
