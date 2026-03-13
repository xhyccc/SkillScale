# SkillScale — Distributed Skill-as-a-Service Agent Infrastructure

A high-performance distributed infrastructure for executing AI agent skills at scale.
SkillScale bridges two industry-standard agent protocols — **Model Context Protocol (MCP)** and **Google Agent-to-Agent (A2A)** — through a unified Rust gateway backed by Kafka (Redpanda), enabling seamless skill discovery, routing, and execution.

> **English** | [中文说明](#中文说明)

---

## Core Principles (核心原理)

### What Problem Does SkillScale Solve?

Modern AI agent ecosystems face a **protocol fragmentation** problem: MCP clients (Claude Desktop, Cursor, etc.) speak one protocol, A2A agents (Google, enterprise platforms) speak another, and the actual skill execution backends need yet another interface. SkillScale solves this with a **three-layer architecture**:

```
  Protocol Layer          →  Gateway Layer        →  Execution Layer
  (MCP / A2A clients)        (Rust, protocol         (Kafka + Skill Servers,
                              translation)             skill discovery & LLM)
```

**Key insight**: The Gateway is a pure **protocol translator** — it speaks MCP and A2A on the outside, but internally everything becomes a Kafka message. This means:

- Adding a new protocol = adding one more HTTP handler in the Gateway
- Adding a new skill = dropping a folder into `skills/` and restarting
- Scaling = adding more Skill Server containers (Kafka handles the distribution)

### How a Request Flows (请求流转原理)

```
 Client                    Rust Gateway              Redpanda            Skill Server
   │                          │                        │                     │
   │── MCP call_tool ────────▶│                        │                     │
   │   or A2A POST            │                        │                     │
   │                          │── Kafka Produce ──────▶│                     │
   │                          │   topic: TOPIC_CODE_   │                     │
   │                          │   ANALYSIS             │                     │
   │                          │   reply_to: REPLY_xxx  │                     │
   │                          │                        │── Kafka Consume ──▶│
   │                          │                        │                     │── parse AGENTS.md
   │                          │                        │                     │── LLM match skill
   │                          │                        │                     │── run.py (stdin→stdout)
   │                          │                        │                     │── LLM review (optional)
   │                          │                        │◀── Kafka Produce ──│
   │                          │                        │   topic: REPLY_xxx  │
   │                          │◀── Kafka Consume ──────│                     │
   │◀── MCP result ──────────│                        │                     │
   │    or A2A response       │                        │                     │
```

**Step-by-step**:

1. **Protocol Ingress** — A client sends a request via MCP (Streamable HTTP on port 8086) or A2A (REST on port 8085). The Rust Gateway parses the protocol-specific envelope.

2. **Topic Routing** — The Gateway maps the request to a Kafka topic:
   - MCP `agent__code-analysis` → topic `TOPIC_CODE_ANALYSIS`
   - MCP `code-analysis__dead-code-detector` → topic `TOPIC_CODE_ANALYSIS` + skill hint
   - A2A `POST /v1/agents/code-analysis/converse` → topic `TOPIC_CODE_ANALYSIS`

3. **Kafka Produce** — The Gateway publishes a JSON message with a unique `request_id` and a `reply_to` topic, then subscribes to the reply topic and waits.

4. **Skill Server Consume** — The Rust Skill Server (one per category) consumes from its topic. It parses the `AGENTS.md` manifest and uses **LLM-powered intent matching** (or keyword fallback) to select the best skill.

5. **Skill Execution** — The matched skill's `scripts/run.py` is invoked as a subprocess (stdin = input, stdout = result). Skills use `llm_utils.py` for LLM calls internally (AST analysis, code review, summarization, etc.).

6. **Response Return** — The result is published to the `reply_to` topic. The Gateway consumes it and formats the response back into MCP or A2A protocol.

### Two Invocation Granularities (两种调用粒度)

| Granularity | MCP Tool Name | A2A Endpoint | Routing |
|-------------|---------------|--------------|---------|
| **Coarse-grained (粗粒度)** | `agent__code-analysis` | `POST /v1/agents/code-analysis/converse` | AGENTS.md + LLM auto-selects the best skill |
| **Fine-grained (细粒度)** | `code-analysis__dead-code-detector` | *(not applicable)* | Directly executes the named skill |

- **Coarse-grained**: The caller only knows the domain ("code analysis"). The Skill Server reads `AGENTS.md` and uses an LLM to match the input to the best specific skill.
- **Fine-grained**: The caller explicitly names the skill. No LLM routing — the skill runs directly.

A2A is **coarse-grained only** by design (agent-level routing). MCP supports **both** modes.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  External Clients                                             │
│                                                               │
│  ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │ Claude Desktop  │  │ Google Agent │  │ Python Scripts  │ │
│  │ Cursor / IDEs   │  │ Enterprise   │  │ LangChain etc.  │ │
│  │ (MCP SSE)       │  │ (A2A REST)   │  │ (MCP or A2A)    │ │
│  └───────┬─────────┘  └──────┬───────┘  └───────┬─────────┘ │
└──────────┼───────────────────┼──────────────────┼────────────┘
           │ :8086/mcp         │ :8085            │
┌──────────▼───────────────────▼──────────────────▼────────────┐
│                   Rust Gateway (axum + rmcp)                  │
│                                                               │
│  MCP Streamable HTTP Server ◄──► A2A REST Server              │
│  (port 8086)                     (port 8085)                  │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Kafka Producer / Consumer (rdkafka)                     │ │
│  │  - Produce to TOPIC_<CATEGORY>                          │ │
│  │  - Consume from REPLY_<request_id>                      │ │
│  └─────────────────────┬───────────────────────────────────┘ │
└────────────────────────┼─────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Redpanda (Kafka)   │
              │  Port 9092          │
              └──────┬─────────┬────┘
                     │         │
      ┌──────────────▼──┐  ┌──▼──────────────────┐
      │  Skill Server   │  │  Skill Server        │
      │  (Rust binary)  │  │  (Rust binary)        │
      │                 │  │                       │
      │  Topic:         │  │  Topic:               │
      │  TOPIC_CODE_    │  │  TOPIC_DATA_          │
      │  ANALYSIS       │  │  PROCESSING           │
      │                 │  │                       │
      │  AGENTS.md      │  │  AGENTS.md            │
      │  ├─ code-       │  │  ├─ text-summarizer   │
      │  │  complexity  │  │  └─ csv-analyzer      │
      │  └─ dead-code-  │  │                       │
      │     detector    │  │                       │
      └─────────────────┘  └───────────────────────┘
```

### Component Responsibilities

| Component | Language | Port | Description |
|-----------|----------|------|-------------|
| **Gateway** | Rust (axum + rmcp) | 8085, 8086 | Protocol translator: A2A REST ↔ Kafka, MCP SSE ↔ Kafka |
| **Redpanda** | — | 9092 | Kafka-compatible message broker, routes topic messages |
| **Skill Server** | Rust + Python | — | Consumes from a Kafka topic, matches skills via AGENTS.md + LLM, executes `run.py` |
| **Skills** | Python | — | Self-contained analysis units: AST parsing + LLM review (`llm_utils.py`) |

---

## Protocol Capabilities

### A2A (Agent-to-Agent)

```
POST http://localhost:8085/v1/agents/{agent_id}/converse
Content-Type: application/json

{
  "id": "task_abc123",
  "sessionId": "session_xyz",
  "message": {
    "role": "user",
    "parts": [{"type": "text", "text": "def foo(): pass"}]
  }
}
```

- **Coarse-grained only** — the `agent_id` in the URL path determines the Kafka topic
- No skill metadata in the payload — the Skill Server decides which skill to run
- Conforms to the [Google A2A Protocol](https://github.com/google/a2a-protocol) standard

### MCP (Model Context Protocol)

```python
# Connect to the MCP SSE endpoint
async with streamablehttp_client("http://localhost:8086/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()

        # Coarse-grained: agent auto-selects skill via AGENTS.md
        result = await session.call_tool("agent__code-analysis", {"input": code})

        # Fine-grained: directly invoke a specific skill
        result = await session.call_tool("code-analysis__dead-code-detector", {"input": code})
```

- **Both coarse and fine-grained** — use `agent__<category>` or `<category>__<skill>`
- Served over **Streamable HTTP (SSE)** on port 8086 — no binary launch needed
- Auto-discovers tools from `skills/` directory on startup

---

## Available Skills

| Skill | Category | Description |
|-------|----------|-------------|
| `code-complexity` | `code-analysis` | Python AST metrics (cyclomatic complexity, nesting depth, function length) + LLM refactoring suggestions |
| `dead-code-detector` | `code-analysis` | AST-based dead code detection (unused imports, empty functions, unreachable code) + LLM cleanup suggestions |
| `text-summarizer` | `data-processing` | LLM-powered text summarization with word/sentence statistics |
| `csv-analyzer` | `data-processing` | Statistical column analysis of CSV data + LLM-generated insights |

---

## Quick Start

### Prerequisites

| Dependency | macOS | Ubuntu/Debian |
|------------|-------|---------------|
| Docker & Docker Compose | `brew install docker` | `apt install docker.io docker-compose-plugin` |
| Python >= 3.10 | `brew install python` | `apt install python3 python3-venv` |

> Rust compilation happens inside Docker — no local Rust toolchain needed.

### One-Command Launch

```bash
./run_all.sh
```

This does everything:
1. Creates `.venv` and installs Python dependencies
2. Runs `build.sh` to generate `docker-compose.yml`, build Docker images, and start services
3. Waits for Gateway (ports 8085 + 8086) to be ready
4. Runs both `demo_a2a_client.py` and `demo_mcp_client.py` to validate the system

On success you'll see:

```
==========================================
    SkillScale System Ready!
==========================================
  • A2A Gateway:    http://localhost:8085
  • MCP Server:     http://localhost:8086/mcp
  • Console (Web):  http://localhost:8080
  • Kafka Broker:   localhost:9092
==========================================
```

### Run Demos Individually

```bash
# A2A demo — coarse-grained agent call
python3 examples/demo_a2a_client.py

# MCP demo — both coarse + fine-grained calls
python3 examples/demo_mcp_client.py
```

### Stop Services

```bash
docker compose down -v    # -v purges Kafka volumes (clean state)
```

---

## Project Structure

```
SkillScale/
├── skillscale-rs/              # Rust workspace (compiled inside Docker)
│   ├── gateway/src/            # Axum HTTP server (A2A + MCP)
│   │   ├── main.rs             # Entry: spawns MCP on 8086, A2A on 8085
│   │   ├── mcp_server.rs       # MCP SSE via rmcp StreamableHttpService
│   │   └── skill_discovery.rs  # Scans skills/ for tool registration
│   ├── skill-server/src/       # Kafka consumer + skill executor
│   │   └── main.rs             # Consumes topic, matches via AGENTS.md, runs skill
│   └── common/src/             # Shared Kafka message types
├── skills/                     # Skill definitions (OpenSkills format)
│   ├── llm_utils.py            # Shared LLM client (Azure/OpenAI/Zhipu)
│   ├── code-analysis/          # Category
│   │   ├── AGENTS.md           # Skill discovery manifest
│   │   └── .claude/skills/
│   │       ├── code-complexity/    # SKILL.md + scripts/run.py
│   │       └── dead-code-detector/ # SKILL.md + scripts/run.py
│   └── data-processing/        # Category
│       ├── AGENTS.md
│       └── .claude/skills/
│           ├── text-summarizer/    # SKILL.md + scripts/run.py
│           └── csv-analyzer/       # SKILL.md + scripts/run.py
├── examples/                   # Ready-to-run demo scripts
│   ├── demo_a2a_client.py      # A2A protocol demo (coarse-grained)
│   └── demo_mcp_client.py      # MCP protocol demo (coarse + fine-grained)
├── docker/                     # Multi-stage Dockerfiles
│   └── Dockerfile.rust         # Builds Gateway + Skill Server + bundles skills
├── build.sh                    # Docker build & launch (generates docker-compose.yml)
├── run_all.sh                  # Full bootstrap: venv + build + launch + validate
└── .env                        # API keys & configuration
```

---

## Configuration

### LLM Provider

All skills share `skills/llm_utils.py`, which reads credentials from `.env`:

```bash
cp .env.example .env   # then fill in your API keys
```

| Provider | Env Vars | Example Model |
|----------|----------|---------------|
| `azure` | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_MODEL` | gpt-4o |
| `openai` | `OPENAI_API_KEY`, `OPENAI_API_BASE`, `OPENAI_MODEL` | DeepSeek-V3 |
| `zhipu` | `ZHIPU_API_KEY`, `ZHIPU_MODEL` | GLM-4.7-FlashX |

Set `LLM_PROVIDER=azure|openai|zhipu` in `.env` to select the active provider.

### Gateway Timeout

The unified timeout (default 600s) is configurable via environment variable:

```bash
SKILLSCALE_GATEWAY_TIMEOUT=600.0   # seconds, used by Gateway + demo clients
```

---

## Message Protocol

All internal communication uses Kafka topic routing with JSON payloads.

**Request** (Gateway → Skill Server):
```json
{
  "request_id": "a1b2c3d4",
  "reply_to":   "REPLY_a1b2c3d4",
  "intent":     "def foo(): pass",
  "timestamp":  1739836800.123
}
```

**Response** (Skill Server → Gateway):
```json
{
  "request_id": "a1b2c3d4",
  "status":     "success",
  "content":    "## Dead Code Report\n..."
}
```

---

## Adding a New Skill

1. Create the skill directory:
   ```
   skills/<category>/.claude/skills/<skill-name>/
   ├── SKILL.md          # Metadata (name, description)
   └── scripts/
       └── run.py        # Entry point: reads stdin, writes stdout
   ```

2. Update `skills/<category>/AGENTS.md` to include the new skill in the `<available_skills>` section.

3. Rebuild: `docker compose build && docker compose up -d`

4. The Gateway auto-discovers the new skill as an MCP tool on next startup.

---

## 中文说明

### 核心原理

SkillScale 解决的核心问题是 **AI Agent 协议碎片化**：MCP 客户端（Claude Desktop、Cursor 等）和 A2A 客户端（Google Agent 平台）使用不同的协议，而底层的技能执行引擎需要统一的接口。

**三层架构**：

1. **协议层** — Rust Gateway 同时暴露 MCP（SSE, 端口 8086）和 A2A（REST, 端口 8085）两种协议接口
2. **消息总线层** — 所有请求统一转换为 Kafka 消息，通过 Redpanda 分发到对应的 Topic
3. **技能执行层** — Rust Skill Server 消费 Kafka 消息，解析 AGENTS.md，通过 LLM 智能匹配最佳技能，执行 `run.py` 并返回结果

### 两种调用粒度

| 粒度 | MCP 工具名 | A2A 端点 | 路由方式 |
|------|-----------|---------|---------|
| **粗粒度** | `agent__code-analysis` | `POST /v1/agents/code-analysis/converse` | 读取 AGENTS.md，LLM 自动选择最佳子技能 |
| **细粒度** | `code-analysis__dead-code-detector` | 不适用 | 直接执行指定的技能，跳过 LLM 路由 |

### 请求流转

```
客户端 ──▶ Rust Gateway ──▶ Redpanda (Kafka) ──▶ Skill Server ──▶ run.py
                                                      │
                                                      ├── 解析 AGENTS.md
                                                      ├── LLM 意图匹配（粗粒度）
                                                      │   或直接执行（细粒度）
                                                      ├── 执行 scripts/run.py
                                                      └── 返回结果 → Kafka → Gateway → 客户端
```

### 快速启动

```bash
# 一键启动（构建 Docker 镜像 + 启动所有服务 + 验证）
./run_all.sh

# 单独运行 Demo
python3 examples/demo_a2a_client.py    # A2A 协议演示
python3 examples/demo_mcp_client.py    # MCP 协议演示（粗粒度 + 细粒度）
```

启动成功后：
- A2A 网关: `http://localhost:8085`
- MCP 服务: `http://localhost:8086/mcp`
- 管理控制台: `http://localhost:8080`

---

## License

MIT
