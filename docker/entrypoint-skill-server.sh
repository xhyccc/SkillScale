#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# entrypoint-skill-server.sh — Container entrypoint for
# SkillScale skill server with OpenCode support
#
# Sets up OpenCode authentication from environment variables,
# then launches the C++ skill server.
# ────────────────────────────────────────────────────────────
set -euo pipefail

# ── 1. Set up OpenCode auth.json from env vars ──
OPENCODE_DATA_DIR="${HOME}/.local/share/opencode"
mkdir -p "$OPENCODE_DATA_DIR"

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    cat > "$OPENCODE_DATA_DIR/auth.json" <<EOF
{"siliconflow":{"type":"api","key":"${OPENAI_API_KEY}"}}
EOF
    echo "[entrypoint] OpenCode auth configured (SiliconFlow provider)"
else
    echo "[entrypoint] WARNING: OPENAI_API_KEY not set — OpenCode execution will fail."
    echo "[entrypoint] Falling back to direct skill execution."
fi

# ── 2. Verify OpenCode installation ──
if command -v opencode &>/dev/null; then
    echo "[entrypoint] OpenCode $(opencode --version 2>/dev/null || echo 'installed')"
else
    echo "[entrypoint] WARNING: OpenCode binary not found in PATH"
fi

# ── 3. Source .env if mounted ──
if [[ -f /app/.env ]]; then
    set -a
    source /app/.env 2>/dev/null || true
    set +a
    echo "[entrypoint] Loaded /app/.env"
fi

# ── 4. Build skill server CLI arguments ──
ARGS=()

# Topic
ARGS+=(--topic "${SKILLSCALE_TOPIC:-TOPIC_DEFAULT}")

# Description (if set)
if [[ -n "${SKILLSCALE_DESCRIPTION:-}" ]]; then
    ARGS+=(--description "$SKILLSCALE_DESCRIPTION")
fi

# Skills directory
ARGS+=(--skills-dir "${SKILLSCALE_SKILLS_DIR:-/skills}")

# Proxy addresses
ARGS+=(--xpub "${SKILLSCALE_PROXY_XPUB:-tcp://proxy:5555}")
ARGS+=(--xsub "${SKILLSCALE_PROXY_XSUB:-tcp://proxy:5444}")

# Workers
ARGS+=(--workers "${SKILLSCALE_WORKERS:-2}")

# Timeout
ARGS+=(--skill-exec-timeout "${SKILLSCALE_TIMEOUT:-180000}")

# HWM
if [[ -n "${SKILLSCALE_HWM:-}" ]]; then
    ARGS+=(--hwm "$SKILLSCALE_HWM")
fi

echo "[entrypoint] Starting skill server: skillscale_skill_server ${ARGS[*]}"

# ── 5. Exec the skill server (replaces shell process) ──
exec skillscale_skill_server "${ARGS[@]}"
