# SkillScale — Distributed Skill-as-a-Service Agent Infrastructure

A middleware SDK and distributed infrastructure for executing AI agent skills at scale.
It enables transparent routing between high-level reasoning protocols—**Model Context Protocol (MCP)** and **Google Agent-to-Agent (A2A)**—and a high-performance Kafka backend.
The backend seamlessly discovers and executes capabilities configured as **Claude Skills** using the
[OpenSkills](https://github.com/numman-ali/openskills) standard.

### Protocol Capabilities (协议能力)

1. **A2A (Agent-to-Agent / REST)**: 
   * **English**: Supports **coarse-grained agent invocation**. The client asks for a general domain (e.g., `code-analysis`), and the backend's LLM routing dynamically matches the intent to the best granular skill.
   * **中文**: 支持**粗粒度的 Agent 调用**。只需要传递领域类的 Agent，后端的 LLM 将会负责动态意图识别并匹配到具体的子技能去执行。

2. **MCP (Model Context Protocol / Stdio)**:
   * **English**: Supports **both coarse-grained agent invocation AND fine-grained skill invocation**. Clients can either rely on the backend agent (e.g., `agent__code-analysis`) to handle routing dynamically via LLM, or they can pinpoint specific explicit skills to execute unconditionally (e.g., `code-analysis__code-complexity`).
   * **中文**: **同时支持细粒度的 Skill 调用和粗粒度的 Agent 调用**。客户端可以按粗粒度调用（例如 `agent__code-analysis`），依赖后端的 LLM 动态路由决策，也可以显式地直接调用具体的原子级工具（例如 `code-analysis__code-complexity`）。

Skill matching is **LLM-powered by default**, and skills themselves function as capable micro-agents. Any established agent framework (**LangChain, LangGraph, CrewAI**) seamlessly interfaces with the network.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│  External Clients & Agent Frameworks                      │
│                                                           │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐ │
│  │ Claude Desktop  │ │ Google Agent │ │ Local Agents  │ │
│  │ (via MCP)       │ │ (via A2A)    │ │ (Python SDK)  │ │
│  └───────┬─────────┘ └──────┬───────┘ └───────┬───────┘ │
└──────────┼──────────────────┼─────────────────┼─────────┘
           │                  │                 │
┌──────────▼──────────────────▼────────┐        │
│    Transparent Rust Gateway          │        │ JSON Intent
│    (Translates MCP & A2A to Kafka)   │        │
└──────────────────┬───────────────────┘        │
                   └───────────┬────────────────┘
                               │
                       Kafka / Redpanda
┌──────────────────────────────▼────────────────────────────┐
│  Redpanda Broker                                          │
└────────────┬──────────────────────────────────┬───────────┘
             │                                  │
 ┌───────────▼──────────────┐    ┌──────────────▼───────────┐
 │  Python Skill Server      │    │  Python Skill Server     │
 │  Topic: DATA_PROCESSING   │    │  Topic: CODE_ANALYSIS    │
 │  AGENTS.md → LLM match    │    │  AGENTS.md → LLM match   │
 │  ├─ text-summarizer       │    │  ├─ code-complexity      │
 │  └─ csv-analyzer          │    │  └─ dead-code-detector   │
 └───────────────────────────┘    └──────────────────────────┘
```

### How It Works

1. **Protocol Bridging** — High-level agents (like Claude Desktop via MCP or Google Engine via A2A) send requests to the Transparent Gateway. The gateway standardizes these into Kafka payloads. Custom agent frameworks (LangChain, CrewAI) can also publish directly via the SDK.
2. **Proxy forwarding** — The stateless Redpanda broker routes topic payloads to the matching Python skill server.
3. **Skill matching** — The Python skill server parses `AGENTS.md` and dynamically matches intents using LLMs or keywords.
4. **Claude Skills execution** — The matched skill's execution layer is invoked using standard Claude OpenSkills (via `scripts/run.py`). The sub-agents themselves call LLMs for intelligent analysis.
5. **Response** — Results are published back over Kafka, through the gateway, and back formatted to the original client protocol (MCP or A2A).

### OpenSkills Integration

SkillScale uses the official [npm openskills](https://www.npmjs.com/package/openskills)
CLI for skill management — the same system used by Claude Code, Cursor, Windsurf, and
other AI coding agents.

```bash
# Install skills from any source
npx openskills install anthropics/skills        # From Anthropic marketplace
npx openskills install ./skills/data-processing/csv-analyzer  # From local path
npx openskills install your-org/your-skills     # From GitHub repo

# Manage skills
npx openskills list                             # List installed skills
npx openskills read <skill-name>                # Load skill content (progressive disclosure)
npx openskills sync                             # Regenerate AGENTS.md

# Runtime flow (Python skill server uses these internally)
# Skill server starts → parses AGENTS.md <available_skills> XML
#   → task arrives on Kafka topic
#   → LLM matching (default) or keyword scoring
#   → npx openskills read <name> loads SKILL.md (progressive disclosure)
#   → scripts/opencode-exec executed with task data on stdin
#   → result published back via Kafka
```

## Dual Intent Modes

SkillScale supports two ways to invoke skills:

| Mode | Name | How It Works |
|------|------|-------------|
| **Mode 1** | **Explicit** | Client sends `{"skill": "csv-analyzer", "data": "..."}` — the skill server runs the named skill directly |
| **Mode 2** | **Task-based** | Client sends `{"task": "analyze this CSV data..."}` or plain text — the skill server **automatically matches** the best installed skill using LLM-powered matching (or keyword fallback) |

Mode 2 is ideal for coarse-grained, resource-oriented routing where the caller doesn't know
(or care) which specific skill handles the request.

```python
# Mode 1 — explicit skill selection
intent = json.dumps({"skill": "text-summarizer", "data": "Long text..."})
result = await client.invoke("TOPIC_DATA_PROCESSING", intent)

# Mode 2 — task-based (server auto-matches via LLM)
result = await client.invoke_task("TOPIC_DATA_PROCESSING", "summarize this article about AI")

# Mode 2 — plain text also works
result = await client.invoke("TOPIC_DATA_PROCESSING", "analyze the CSV data: a,b\n1,2")
```


SkillScale includes a **Transparent Gateway** bridging external Model Context Protocol (MCP) and Google Agent-to-Agent (A2A) networks down into the blazing-fast internal Kafka bus.

```bash
./run_all.sh       # Starts Docker services + Rust Gateway
```

| Bridge | Description |
|-----|-------------|
| **Google A2A** | REST server mapped via Pydantic conforming to `a2a-protocol` standard |
| **Model Context Protocol** | StdIO server allowing Claude Desktop and similar clients to call internal skills |

## Project Structure

```
SkillScale/
├── skillscale/                 # Python SDK (the middleware)
│   ├── __init__.py             # Public API: SkillScaleClient, SkillDiscovery
│   ├── client.py               # Core async Kafka client
│   ├── discovery.py            # AGENTS.md scanner & metadata registry
│   └── adapters/
│       ├── langchain.py        # LangChain tools + toolkit
│       ├── langgraph.py        # LangGraph nodes + graph factory
│       └── crewai.py           # CrewAI tool adapter
├── skillscale-rs/              # Rust workspace
│   ├── gateway/                # Axum REST (A2A) + rmcp (stdIO) server
│   │   └── src/                # Translated payload routers
│   └── shared/                 # Common libs
├── examples/                   # Ready-to-run agent examples
│   ├── direct_client.py        # Raw SDK usage (no framework)
│   ├── langchain_agent.py      # LangChain ReAct agent
│   └── langgraph_agent.py      # LangGraph state graph
├── scripts/                    # Shared tooling
│   ├── openskills              # Thin wrapper → npx openskills (npm CLI)
│   ├── opencode-exec           # Executable router for matched skills
│   └── prompts/
│       └── skill_match.txt     # Configurable prompt template for LLM matching
├── agent/                      # Python skill servers & LLM matchers
│   ├── main_kafka.py           # Main Kafka workers mapping topic -> skills
│   └── requirements.txt
├── skills/                     # Portable skill definitions (OpenSkills format)
│   ├── llm_utils.py            # Shared LLM client (Azure/OpenAI/Zhipu)
│   ├── data-processing/
│   │   ├── AGENTS.md           # OpenSkills discovery manifest
│   │   └── text-summarizer/    # SKILL.md + scripts/run.py
│   └── code-analysis/
│       ├── AGENTS.md           # OpenSkills discovery manifest
│       └── code-complexity/    # SKILL.md + scripts/run.py
├── docker/                     # Multi-stage Dockerfiles
├── k8s/                        # Kubernetes manifests + CRDs + KEDA
├── requirements.txt            # All Python dependencies (unified)
├── run_all.sh        # Bootstrap and Launch SkillScale System
└── build.sh      # Docker build & launch
```

## Components

| Component | Language | Description |
|-----------|----------|-------------|
| **skillscale/** | Python 3.10+ | **Middleware SDK** — core Kafka client, skill discovery, and framework adapters |
| **skillscale-rs/** | Rust | Transparent Gateway bridging Model Context Protocol (MCP stdio) and Google Agent-to-Agent (A2A HTTPS) down into internal Kafka. |
| **agent/** | Python 3.10+ | Python skill servers; parses `AGENTS.md` for OpenSkills discovery; matches tasks via LLM |
| **scripts/** | Python + Shell | `npx openskills` wrapper, LLM execution pipelines, prompt templates |
| **examples/** | Python | Working examples: direct client, LangChain agent, LangGraph graph |
| **skills/** | Markdown + Python | `AGENTS.md` discovery manifests, `SKILL.md` metadata, `scripts/opencode-exec` LLM-powered executables, shared `llm_utils.py` |
| **k8s/** | YAML | Namespace, Deployments, Services, SkillTopic CRD, KEDA ScaledObjects |

## Available Skills

| Skill | Topic | Description |
|-------|-------|-------------|
| `text-summarizer` | `TOPIC_DATA_PROCESSING` | LLM-powered text summarization with word/sentence statistics |
| `csv-analyzer` | `TOPIC_DATA_PROCESSING` | Statistical analysis of CSV data + LLM-generated insights and recommendations |
| `code-complexity` | `TOPIC_CODE_ANALYSIS` | Python AST-based metrics (cyclomatic complexity, nesting depth) + LLM refactoring suggestions |
| `dead-code-detector` | `TOPIC_CODE_ANALYSIS` | AST-based dead code detection (unused imports, unreachable code) + LLM cleanup suggestions |

## LLM Configuration

All skills, the routing agent, and the LLM skill matcher share `skills/llm_utils.py`,
which reads API credentials from the project-root `.env` file.

```bash
cp .env.example .env   # then fill in your API keys
```

| Provider | Env Vars | Example Model |
|----------|----------|---------------|
| `azure` | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_MODEL`, `AZURE_API_VERSION` | gpt-4o |
| `openai` | `OPENAI_API_KEY`, `OPENAI_API_BASE`, `OPENAI_MODEL` | DeepSeek-V3.1-Terminus |
| `zhipu` | `ZHIPU_API_KEY`, `ZHIPU_MODEL` | GLM-4.7-FlashX |

Set `LLM_PROVIDER=azure|openai|zhipu` in `.env` to select the active provider.

## Quick Start

### Prerequisites

| Dependency | macOS (Homebrew) | Ubuntu/Debian |
|------------|------------------|---------------|
| Rust / Cargo | `rustup-init` | `rustup-init` |
| Python >= 3.10 | `brew install python` | `apt install python3 python3-venv` |
| Node.js >= 20.6 | `brew install node` | `apt install nodejs npm` |
| Docker & Docker Compose | `brew install docker` | `apt install docker.io docker-compose-plugin` |

### One-Command Install

```bash
bash ./run_all.sh
```

This does everything:
- Creates a Python virtual environment and installs all dependencies
- Installs the `openskills` npm CLI and registers all 4 skills
- Generates `AGENTS.md` files via `npx openskills sync`
- Installs the `skillscale` SDK in development mode

```bash
./run_all.sh
```

This starts **everything**:
- Kafka/Redpanda infrastructure and Python skill servers via Docker Compose
- Transparent Rust Gateway (MCP & A2A Bridge) routing topics locally

Press `Ctrl+C` to stop all services.

### Install the SDK

```bash
# From the repo root (editable / development mode)
pip install -e ".[dev]"

# With framework adapters
pip install -e ".[langchain]"     # LangChain support
pip install -e ".[langgraph]"     # LangGraph support
pip install -e ".[crewai]"        # CrewAI support
pip install -e ".[all]"           # everything
```

### Run Locally (manual)

Start each component in separate terminals:

```bash
# Terminal 1: Kafka Message Broker
docker-compose -f docker-compose.yml up -d redpanda

# Terminal 2: Skill Server (data-processing, LLM matching)
source .venv/bin/activate
python3 agent/main_kafka.py \
  --topic TOPIC_DATA_PROCESSING \
  --skills-dir ./skills/data-processing \
  --matcher llm

# Terminal 3: Skill Server (code-analysis, LLM matching)
source .venv/bin/activate
python3 agent/main_kafka.py \
  --topic TOPIC_CODE_ANALYSIS \
  --skills-dir ./skills/code-analysis \
  --matcher llm

# Terminal 4: Rust Gateway
cd skillscale-rs/gateway && cargo run --release
```

## Configuration

### Environment Variables

All configuration is centralized in `.env` (see `.env.example` for all options):

```bash
cp .env.example .env   # then fill in your API keys and tune parameters
```

```dotenv
# ──────────────────────────────────────────────────────────
#  SkillScale — Configuration
# ──────────────────────────────────────────────────────────
#
# Copy this file to .env and fill in your API keys:
#   cp .env.example .env
#

# ══════════════════════════════════════════════════════════
#  LLM Providers
# ══════════════════════════════════════════════════════════

# ── Active Provider Selection ─────────────────────────────
# Options: openai, azure, zhipu
LLM_PROVIDER=openai

# ── OpenAI / SiliconFlow Configuration ────────────────────
OPENAI_API_KEY=sk-your-openai-api-key-here
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# ── Azure OpenAI Configuration ───────────────────────────
AZURE_API_KEY=your-azure-api-key-here
AZURE_API_BASE=https://your-resource.openai.azure.com/
AZURE_API_VERSION=2024-12-01-preview
AZURE_MODEL=gpt-4o

# ── Zhipu AI Configuration ───────────────────────────────
ZHIPU_API_KEY=your-zhipu-api-key-here
ZHIPU_API_BASE=https://open.bigmodel.cn/api/paas/v4
ZHIPU_MODEL=GLM-4.7-FlashX

# ══════════════════════════════════════════════════════════
#  Skill Server
# ══════════════════════════════════════════════════════════

# Concurrent worker threads per skill server container
SKILLSCALE_WORKERS=2

# Skill execution timeout (ms) — how long Python skill server waits
# for OpenCode/skill subprocess to complete
SKILLSCALE_TIMEOUT=300000

# ZMQ high-water mark (max queued messages per socket)
SKILLSCALE_HWM=10000

# ZMQ heartbeat interval (ms)
SKILLSCALE_HEARTBEAT=5000

# Skill matching mode: "llm" or "keyword"
SKILLSCALE_MATCHER=llm

# Python executable for running skill scripts
SKILLSCALE_PYTHON=python3

# ══════════════════════════════════════════════════════════
#  Proxy
# ══════════════════════════════════════════════════════════

# Proxy bind addresses and metrics port
SKILLSCALE_XSUB_BIND=tcp://*:5444
SKILLSCALE_XPUB_BIND=tcp://*:5555
SKILLSCALE_METRICS_PORT=9100

# ══════════════════════════════════════════════════════════
#  Client / Agent
# ══════════════════════════════════════════════════════════

# Publish timeout (seconds) — how long the ZMQ client waits
# for a response after publishing to the message queue
PUBLISH_TIMEOUT=300

# Client connect addresses (used by agent and Python SDK)
SKILLSCALE_PROXY_XSUB=tcp://127.0.0.1:5444
SKILLSCALE_PROXY_XPUB=tcp://127.0.0.1:5555

# Subscription settle time (seconds) — delay for ZMQ sub propagation
SKILLSCALE_SETTLE_TIME=0.5
```

### Skill Server CLI Arguments

```
./skillscale_skill_server [OPTIONS]

  --topic <TOPIC>         Kafka topic to subscribe to
  --description <DESC>    Human-readable server description
  --skills-dir <DIR>      Path to skills directory (containing AGENTS.md)
  --matcher <MODE>        Skill matching strategy: llm (default) or keyword
  --python <PATH>         Python executable for LLM matching subprocess
  --prompt-file <PATH>    Custom prompt template for LLM matching
  --proxy-xpub <ADDR>     Proxy XPUB address (default: tcp://127.0.0.1:5555)
  --proxy-xsub <ADDR>     Proxy XSUB address (default: tcp://127.0.0.1:5444)
  --workers <N>           Number of worker threads (default: 2)
  --timeout <MS>          Skill execution timeout in ms (default: 30000)
```

## Message Protocol

All messages use standard Kafka topic routing with JSON payloads.

**Request** (Agent -> Skill Server via Kafka):
```json
// Topic: "TOPIC_DATA_PROCESSING" 
{
  "request_id": "a1b2c3d4",
  "reply_to":   "AGENT_REPLY_9f8e7d6c",
  "intent":     "...",
  "timestamp":  1739836800.123
}
```

**Response** (Skill Server -> Agent via Kafka):
```json
// Topic: "AGENT_REPLY_9f8e7d6c" 
{
  "request_id": "a1b2c3d4",
  "status":     "success",
  "content":    "## CSV Analysis\n..."
}
```

## Kubernetes Deployment

```bash
kubectl apply -f k8s/
```

## Using the Middleware SDK

### Direct Client (no framework)

```python
import asyncio, json
from skillscale import SkillScaleClient

async def main():
    async with SkillScaleClient() as client:
        # Mode 1: explicit skill selection
        intent = json.dumps({"skill": "text-summarizer", "data": "Some long text..."})
        result = await client.invoke("TOPIC_DATA_PROCESSING", intent)
        print(result)

        # Mode 2: task-based (server auto-matches via LLM)
        result = await client.invoke_task("TOPIC_DATA_PROCESSING",
                                          "summarize this article about AI progress")
        print(result)

asyncio.run(main())
```

### LangChain

```python
from skillscale import SkillScaleClient
from skillscale.adapters.langchain import SkillScaleToolkit

client = SkillScaleClient()
await client.connect()

toolkit = SkillScaleToolkit.from_skills_dir(client, "./skills")
tools = toolkit.get_tools()            # Mode 1: one tool per skill
task_tools = toolkit.get_task_tools()  # Mode 2: one tool per topic
```

### LangGraph

```python
from skillscale.adapters.langgraph import SkillScaleGraph

sg = SkillScaleGraph.from_skills_dir(client, "./skills")
graph = sg.build_graph(llm=my_llm)     # Mode 1: LLM picks the skill
graph = sg.build_graph(task_based=True) # Mode 2: Python server matches via LLM
```

### CrewAI

```python
from skillscale.adapters.crewai import SkillScaleCrewTools

crew_tools = SkillScaleCrewTools.from_skills_dir(client, "./skills")
tools = crew_tools.get_all_tools()     # both modes
agent = Agent(role="analyst", tools=tools, ...)
```

## Adding a New Skill

1. Create a directory with the skill definition:
   ```
   skills/<category>/<skill-name>/
   +-- SKILL.md
   +-- scripts/
       +-- run.py
   ```

2. Write `SKILL.md` with YAML frontmatter:
   ```yaml
   ---
   name: my-new-skill
   description: One-line description of what this skill does.
   license: MIT
   compatibility: python3
   allowed-tools: python3
   ---
   # My New Skill
   ## Purpose
   ...
   ```

3. Create `scripts/run.py` — reads from stdin, writes markdown to stdout.

4. Install and register via OpenSkills:
   ```bash
   npx openskills install ./skills/<category>/<skill-name>
   npx openskills sync    # regenerate AGENTS.md
   ```

5. The skill server auto-discovers skills on startup by parsing `AGENTS.md`.

## License

MIT
