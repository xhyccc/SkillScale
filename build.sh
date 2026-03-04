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
#   bash build.sh              # full clean + build + launch (Docker only)
#   bash build.sh --no-clean   # build + launch (skip cleaning)
#   bash build.sh --build-only # clean + build images only
#   bash build.sh --down       # stop all containers
#
# NOTE: For a complete system launch (including the Python Gateway), 
# use ./run_all.sh instead.
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

log "OPENAI_API_BASE=$OPENAI_API_BASE"
log "OPENAI_MODEL=$OPENAI_MODEL"
# Apply defaults for optional params
SKILLSCALE_WORKERS="${SKILLSCALE_WORKERS:-2}"
SKILLSCALE_HWM="${SKILLSCALE_HWM:-10000}"
SKILLSCALE_HEARTBEAT="${SKILLSCALE_HEARTBEAT:-5000}"
SKILLSCALE_MATCHER="${SKILLSCALE_MATCHER:-llm}"
SKILLSCALE_PYTHON="${SKILLSCALE_PYTHON:-python3}"
SKILLSCALE_XSUB_BIND="${SKILLSCALE_XSUB_BIND:-tcp://*:5444}"
SKILLSCALE_XPUB_BIND="${SKILLSCALE_XPUB_BIND:-tcp://*:5555}"
SKILLSCALE_METRICS_PORT="${SKILLSCALE_METRICS_PORT:-9100}"
SKILLSCALE_PROXY_XSUB="${SKILLSCALE_PROXY_XSUB:-tcp://127.0.0.1:5444}"
SKILLSCALE_PROXY_XPUB="${SKILLSCALE_PROXY_XPUB:-tcp://127.0.0.1:5555}"
SKILLSCALE_SETTLE_TIME="${SKILLSCALE_SETTLE_TIME:-0.5}"

log "LLM_PROVIDER=$LLM_PROVIDER"
log "SKILLSCALE_WORKERS=$SKILLSCALE_WORKERS"
log "SKILLSCALE_HWM=$SKILLSCALE_HWM"
log "SKILLSCALE_TIMEOUT=${SKILLSCALE_TIMEOUT}ms"
log "PUBLISH_TIMEOUT=${PUBLISH_TIMEOUT}s"
log "SKILLSCALE_MATCHER=$SKILLSCALE_MATCHER"

# ════════════════════════════════════════════════════════════
# 2. Clean compiled artifacts
# ════════════════════════════════════════════════════════════
if ! $NO_CLEAN; then
    step "Cleaning compiled artifacts"

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
        "baseURL": "${OPENAI_API_BASE}",
        "apiKey": "${OPENAI_API_KEY}"
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
ENV_BLOCK='      SKILLSCALE_BROKER_URL: "redpanda:29092"
      SKILLSCALE_ROOT: "/app"
      OPENAI_API_KEY: "'"${OPENAI_API_KEY}"'"
      OPENAI_API_BASE: "'"${OPENAI_API_BASE}"'"
      LLM_PROVIDER: "'"${LLM_PROVIDER}"'"'

# Add optional provider keys if set
[[ -n "${AZURE_API_KEY:-}" ]]     && ENV_BLOCK+=$'\n''      AZURE_API_KEY: "'"${AZURE_API_KEY}"'"'
[[ -n "${AZURE_API_BASE:-}" ]]    && ENV_BLOCK+=$'\n''      AZURE_API_BASE: "'"${AZURE_API_BASE}"'"'
[[ -n "${AZURE_MODEL:-}" ]]       && ENV_BLOCK+=$'\n''      AZURE_MODEL: "'"${AZURE_MODEL}"'"'
[[ -n "${AZURE_API_VERSION:-}" ]] && ENV_BLOCK+=$'\n''      AZURE_API_VERSION: "'"${AZURE_API_VERSION}"'"'
[[ -n "${ZHIPU_API_KEY:-}" ]]     && ENV_BLOCK+=$'\n''      ZHIPU_API_KEY: "'"${ZHIPU_API_KEY}"'"'
[[ -n "${ZHIPU_MODEL:-}" ]]       && ENV_BLOCK+=$'\n''      ZHIPU_MODEL: "'"${ZHIPU_MODEL}"'"'

# Start composing
cat > "$COMPOSE_FILE" <<'HEADER'
# SkillScale — Auto-generated Docker Compose (Rust + Redpanda)
# Generated by build.sh — DO NOT EDIT MANUALLY
#
# Topology:
#   Gateway ──▶ Redpanda ◀── Skill Server (Rust)
#

services:
  # ── Redpanda Broker ──
  redpanda:
    image: docker.redpanda.com/redpandadata/redpanda:v23.3.6
    command:
      - redpanda
      - start
      - --smp 1
      - --memory 1G
      - --reserve-memory 0M
      - --overprovisioned
      - --node-id 0
      - --check=false
      - --kafka-addr PLAINTEXT://0.0.0.0:29092,OUTSIDE://0.0.0.0:9092
      - --advertise-kafka-addr PLAINTEXT://redpanda:29092,OUTSIDE://localhost:9092
      - --pandaproxy-addr 0.0.0.0:8082
      - --advertise-pandaproxy-addr localhost:8082
      # Auto-create topics to prevent "UnknownTopicOrPartition" errors
      - --set redpanda.auto_create_topics_enabled=true
    ports:
      - "8081:8081"
      - "8082:8082"
      - "9092:9092"
      - "9644:9644"
    volumes:
      - redpanda-data:/var/lib/redpanda/data
    healthcheck:
      test: ["CMD-SHELL", "rpk cluster health | grep -q 'Healthy:.*true'"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ── Redpanda Console (Web UI) ──
  console:
    image: docker.redpanda.com/redpandadata/console:v2.4.3
    entrypoint: /bin/sh
    command: -c "echo \"$$CONSOLE_CONFIG_FILE\" > /tmp/config.yaml && /app/console"
    environment:
      CONFIG_FILEPATH: /tmp/config.yaml
      CONSOLE_CONFIG_FILE: |
        kafka:
          brokers: ["redpanda:29092"]
          schemaRegistry:
            enabled: false
        redpanda:
          adminApi:
            enabled: true
            urls: ["http://redpanda:9644"]
    ports:
      - "8080:8080"
    depends_on:
      redpanda:
        condition: service_healthy

  # ── Rust Gateway ──
  gateway:
    build:
      context: .
      dockerfile: docker/Dockerfile.rust
    command: ["gateway"]
    environment:
      SKILLSCALE_BROKER_URL: "redpanda:29092"
      SKILLSCALE_GATEWAY_TIMEOUT: "180.0"
      RUST_LOG: "info,gateway=debug"
    ports:
      - "8085:8085"
    depends_on:
      redpanda:
        condition: service_healthy
    restart: unless-stopped

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
  # ── Skill Server (Rust): ${name} ──
  ${svc_name}:
    build:
      context: .
      dockerfile: docker/Dockerfile.rust
    command: ["skill-server"]
    environment:
      SKILLSCALE_TOPIC: "${topic}"
      SKILLSCALE_GROUP_ID: "skill-server-group-${name}"
${ENV_BLOCK}
    volumes:
      - ./${dir}:/app/skills/${name}:ro
      - ./${dir}/AGENTS.md:/app/AGENTS.md:ro
      - ./skills:/app/skills:ro
      - ./opencode.json:/root/.config/opencode/config.json:ro
      - ./.claude/skills:/app/.claude/skills:ro
    depends_on:
      redpanda:
        condition: service_healthy
      # Gateway depends on this, ensuring topic exists before skill server connects?
      # No, circular dep. The ultimate fix is enabling auto_create_topics on redpanda.
    restart: unless-stopped

SKILL_SVC

    log "Generated service: ${svc_name}"
done

cat >> "$COMPOSE_FILE" <<VOLUMES
volumes:
  redpanda-data:
VOLUMES

log "Generated docker-compose.yml with ${#SKILL_DIRS[@]} skill server(s) + gateway + redpanda."

# ════════════════════════════════════════════════════════════
# 5. Ensure Docker is running
# ════════════════════════════════════════════════════════════
step "Checking Docker"

if ! docker info &>/dev/null; then
    warn "Docker daemon not running."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        log "Attempting to start Docker Desktop (macOS)..."
        open -a Docker 2>/dev/null || true
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        log "Please start Docker manually (e.g., sudo systemctl start docker)"
    fi
    for i in {1..30}; do
        docker info &>/dev/null && break
        sleep 2
    done
    if ! docker info &>/dev/null; then
        err "Docker daemon failed to start. Please start Docker manually."
        exit 1
    fi
fi
log "Docker is running."

# ════════════════════════════════════════════════════════════
# 6. Stop existing containers
# ════════════════════════════════════════════════════════════
step "Stopping existing containers"

# Stop default project
docker compose down --remove-orphans 2>/dev/null || true

# Stop potential rust project (skillscale-rust) if running
if docker ps -a --format '{{.Names}}' | grep -q "skillscale-rust"; then
    log "Stopping skillscale-rust containers..."
    # Try using compose down first if file exists
    if [[ -f docker-compose-rust.yml ]]; then
        docker compose -f docker-compose-rust.yml -p skillscale-rust down --remove-orphans 2>/dev/null || true
    fi
    # Force kill leftover containers with that project label
    docker ps -aq --filter label=com.docker.compose.project=skillscale-rust | xargs -r docker rm -f 2>/dev/null || true
fi

log "Old containers stopped."

# ════════════════════════════════════════════════════════════
# 7. Build all Docker images
# ════════════════════════════════════════════════════════════
step "Building Docker images"

log "Building Rust Gateway & Skill Servers..."
docker compose build gateway 2>&1

for svc in "${SKILL_SERVICE_NAMES[@]}"; do
    log "Ensuring ${svc} is built..."
    docker compose build "$svc" 2>&1 | tail -3
done

log "All images built successfully."

# ════════════════════════════════════════════════════════════
# 8. Launch (unless --build-only)
# ════════════════════════════════════════════════════════════
if $BUILD_ONLY; then
    step "Build complete (--build-only)"
    log "Run 'docker compose up -d' to start services."
    exit 0
fi

step "Launching Redpanda Infrastructure"
# Start only Redpanda first to ensure topics are ready before consumers start
docker compose up -d redpanda console 2>&1

log "Waiting for Redpanda to settle..."

# Loop until Redpanda is healthy and responds to rpk
MAX_RETRIES=30
for ((i=1; i<=MAX_RETRIES; i++)); do
    if docker compose exec redpanda rpk cluster info >/dev/null 2>&1; then
        log "Redpanda is ready."
        break
    fi
    echo -n "."
    sleep 2
    if [[ $i -eq $MAX_RETRIES ]]; then
        err "Redpanda failed to become ready after 60s."
        exit 1
    fi
done

log "Creating Kafka topics (skill.request)..."
if docker compose exec redpanda rpk topic create skill.request -r 1 -p 1 2>/dev/null; then
    log "Topic 'skill.request' created."
else
    log "Topic 'skill.request' likely already exists."
fi

step "Launching Skill Servers & Gateway"
# Now start the consumers/producers which depend on the topic
docker compose up -d 2>&1


# ════════════════════════════════════════════════════════════
# 9. Status report
# ════════════════════════════════════════════════════════════
step "Service Status"
docker compose ps

echo ""
log "${GREEN}${BOLD}SkillScale (Rust + Redpanda) is running!${NC}"
echo ""
echo -e "  ${CYAN}Gateway A2A/HTTP:${NC}  http://localhost:8085"
echo -e "  ${CYAN}Redpanda Kafka:${NC}    localhost:9092"
echo -e "  ${CYAN}Redpanda Console:${NC}  http://localhost:8080"
echo ""
echo -e "  ${CYAN}Skill servers:${NC}"
for i in "${!SKILL_NAMES[@]}"; do
    echo -e "    • ${BOLD}${SKILL_NAMES[$i]}${NC}  →  ${SKILL_TOPICS[$i]}"
done
echo ""
echo -e "  ${CYAN}Commands:${NC}"
echo "    ./run_all.sh                    # launch complete system + gateway"
echo "    docker compose logs -f          # follow all logs"
echo "    docker compose logs <service>   # single service logs"
echo "    docker compose down             # stop everything"
echo "    bash build.sh --down            # stop everything"
echo ""
