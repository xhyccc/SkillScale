# SkillScale — Distributed Skill-as-a-Service Agent Infrastructure

A middleware SDK and distributed infrastructure for executing AI agent skills at scale.
C++ Skill Servers discover and execute skills using the
[OpenSkills](https://github.com/numman-ali/openskills) standard over a
ZeroMQ pub/sub bus. Skill matching is **LLM-powered by default** (with keyword fallback),
and skills themselves call LLMs for intelligent analysis. Any agent framework —
**LangChain, LangGraph, CrewAI**, or your own — plugs into the middleware through
thin adapters.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│  Your Agent (pick any framework)                          │
│  LangChain │ LangGraph │ CrewAI │ AutoGen │ Custom        │
└────────────────────────┬──────────────────────────────────┘
                         │  pip install skillscale
┌────────────────────────▼──────────────────────────────────┐
│  skillscale SDK  (middleware)                              │
│  ┌───────────────┐  ┌────────────┐  ┌──────────────────┐  │
│  │ Core Client    │  │ Adapters   │  │ Skill Discovery  │  │
│  │ (async ZMQ)    │  │ LC/LG/Crew │  │ (AGENTS.md scan) │  │
│  └───────────────┘  └────────────┘  └──────────────────┘  │
└────────────────────────┬──────────────────────────────────┘
                         │  ZeroMQ PUB/SUB
┌────────────────────────▼──────────────────────────────────┐
│  C++ XPUB/XSUB Proxy (:5444 XSUB │ :5555 XPUB │ :9100)  │
└────────────┬──────────────────────────────────┬───────────┘
             │                                  │
 ┌───────────▼──────────────┐    ┌──────────────▼───────────┐
 │  C++ Skill Server         │    │  C++ Skill Server        │
 │  Topic: DATA_PROCESSING   │    │  Topic: CODE_ANALYSIS    │
 │  AGENTS.md → LLM match    │    │  AGENTS.md → LLM match   │
 │  ├─ text-summarizer       │    │  ├─ code-complexity      │
 │  └─ csv-analyzer          │    │  └─ dead-code-detector   │
 └───────────────────────────┘    └──────────────────────────┘
```

### How It Works

1. **Agent routing** — Your agent framework publishes a JSON intent to a ZMQ topic
   (e.g. `TOPIC_DATA_PROCESSING`). An LLM classifies the intent and selects the topic.
2. **Proxy forwarding** — The stateless XPUB/XSUB proxy forwards it to the matching
   C++ skill server.
3. **Skill matching** — The C++ skill server dispatches the task to
   [OpenCode](https://github.com/opencode-ai/opencode), which reads `AGENTS.md`
   to discover available skills and automatically matches + executes the best one.
4. **Skill execution** — The matched skill's `scripts/run.py` is executed via POSIX
   `fork`/`exec`. Skills themselves call LLMs for intelligent analysis.
5. **Response** — Results are published back on the agent's ephemeral reply topic
   (`AGENT_REPLY_<id>`).

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

# Runtime flow (C++ skill server uses these internally)
# Skill server starts → parses AGENTS.md <available_skills> XML
#   → task arrives on ZMQ topic
#   → LLM matching (default) or keyword scoring
#   → npx openskills read <name> loads SKILL.md (progressive disclosure)
#   → scripts/run.py executed with task data on stdin
#   → result published back via ZMQ
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

## Web UI

SkillScale includes a unified management and testing interface:

```bash
./launch_ui.sh       # Starts everything + opens browser
```

| Tab | Description |
|-----|-------------|
| **Dashboard** | Launch/stop/restart proxy and skill servers, view logs and configuration |
| **Chat Testing** | Send test messages to any topic, see results with inline request traces |
| **Traces** | Full request lifecycle view with waterfall timing charts across all phases |

The UI runs on `http://localhost:3001` (frontend) with the API on `http://localhost:8401`.

## Project Structure

```
SkillScale/
├── skillscale/                 # Python SDK (the middleware)
│   ├── __init__.py             # Public API: SkillScaleClient, SkillDiscovery
│   ├── client.py               # Core async ZMQ pub/sub client
│   ├── discovery.py            # AGENTS.md scanner & metadata registry
│   └── adapters/
│       ├── langchain.py        # LangChain tools + toolkit
│       ├── langgraph.py        # LangGraph nodes + graph factory
│       └── crewai.py           # CrewAI tool adapter
├── examples/                   # Ready-to-run agent examples
│   ├── direct_client.py        # Raw SDK usage (no framework)
│   ├── langchain_agent.py      # LangChain ReAct agent
│   └── langgraph_agent.py      # LangGraph state graph
├── proxy/                      # C++ XPUB/XSUB proxy
│   ├── main.cpp
│   └── CMakeLists.txt
├── skill-server/               # C++ skill server (OpenSkills)
│   ├── main.cpp                # Worker thread pool, ZMQ sockets, CLI config
│   ├── skill_loader.cpp/h      # AGENTS.md parser, LLM/keyword matching
│   ├── skill_executor.cpp/h    # POSIX fork/exec with timeout
│   ├── message_handler.cpp/h   # JSON envelope serialization
│   └── CMakeLists.txt
├── scripts/                    # Shared tooling
│   ├── openskills              # Thin wrapper → npx openskills (npm CLI)
│   ├── llm_match.py            # LLM-powered skill matching (Python subprocess)
│   └── prompts/
│       └── skill_match.txt     # Configurable prompt template for LLM matching
├── agent/                      # Standalone CLI agent (uses SDK)
│   ├── main.py
│   └── requirements.txt
├── skills/                     # Portable skill definitions (OpenSkills format)
│   ├── llm_utils.py            # Shared LLM client (Azure/OpenAI/Zhipu)
│   ├── data-processing/
│   │   ├── AGENTS.md           # OpenSkills discovery manifest
│   │   ├── text-summarizer/    # SKILL.md + scripts/run.py
│   │   └── csv-analyzer/       # SKILL.md + scripts/run.py
│   └── code-analysis/
│       ├── AGENTS.md           # OpenSkills discovery manifest
│       ├── code-complexity/    # SKILL.md + scripts/run.py
│       └── dead-code-detector/ # SKILL.md + scripts/run.py
├── ui/                         # Unified web interface
│   └── management/
│       ├── server.py           # FastAPI backend (API + chat + tracing)
│       └── frontend/           # React + Vite frontend (3 tabs)
├── tests/                      # 34 integration & fault-tolerance tests
├── docker/                     # Multi-stage Dockerfiles
├── k8s/                        # Kubernetes manifests + CRDs + KEDA
├── package.json                # npm openskills dependency
├── requirements.txt            # All Python dependencies (unified)
├── setup.sh                    # One-command install
├── launch_ui.sh                # Launch everything + open browser
└── launch_all.sh               # Launch services + run E2E test
```

## Components

| Component | Language | Description |
|-----------|----------|-------------|
| **skillscale/** | Python 3.10+ | **Middleware SDK** — core ZMQ client, skill discovery, and framework adapters (LangChain, LangGraph, CrewAI) |
| **proxy/** | C++17 | XPUB/XSUB stateless message switch with Prometheus metrics on `:9100` |
| **skill-server/** | C++17 | Multi-threaded subscriber; parses `AGENTS.md` for OpenSkills discovery; matches tasks via LLM (default) or keyword scoring; executes skills via POSIX `fork`/`exec` with configurable timeouts |
| **scripts/** | Python + Shell | `npx openskills` wrapper, LLM skill matcher (`llm_match.py`), configurable prompt templates |
| **agent/** | Python 3.10+ | Standalone CLI agent built on the SDK; LLM-powered topic routing |
| **examples/** | Python | Working examples: direct client, LangChain agent, LangGraph graph |
| **skills/** | Markdown + Python | `AGENTS.md` discovery manifests, `SKILL.md` metadata, `scripts/run.py` LLM-powered executables, shared `llm_utils.py` |
| **ui/** | Python + React | Unified management UI — Dashboard (service control), Chat Testing (interactive), Traces (request lifecycle waterfall) |
| **tests/** | Python (pytest) | 34 tests covering proxy routing, agent E2E, skill execution, and fault tolerance |
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
| CMake >= 3.16 | `brew install cmake` | `apt install cmake` |
| libzmq | `brew install zeromq` | `apt install libzmq3-dev` |
| cppzmq | `brew install cppzmq` | `apt install libzmq3-dev` (headers included) |
| nlohmann-json | `brew install nlohmann-json` | `apt install nlohmann-json3-dev` |
| pkg-config | `brew install pkg-config` | `apt install pkg-config` |
| Python >= 3.10 | `brew install python` | `apt install python3 python3-venv` |
| Node.js >= 20.6 | `brew install node` | `apt install nodejs npm` |

### One-Command Install

```bash
chmod +x setup.sh && ./setup.sh
```

This does everything:
- Creates a Python virtual environment and installs all dependencies
- Installs the `openskills` npm CLI and registers all 4 skills
- Generates `AGENTS.md` files via `npx openskills sync`
- Builds both C++ binaries (proxy + skill server)
- Installs the `skillscale` SDK in development mode

### One-Command Launch (with UI)

```bash
chmod +x launch_ui.sh && ./launch_ui.sh
```

This starts **everything** and opens the browser:
- C++ XPUB/XSUB proxy
- 2 C++ skill servers (data-processing + code-analysis)
- FastAPI backend (port 8401)
- React frontend (port 3001)
- Opens `http://localhost:3001` in your browser

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
# Terminal 1: XPUB/XSUB Proxy
./proxy/build/skillscale_proxy

# Terminal 2: Skill Server (data-processing, LLM matching)
./skill-server/build/skillscale_skill_server \
  --topic TOPIC_DATA_PROCESSING \
  --skills-dir ./skills/data-processing \
  --matcher llm \
  --python .venv/bin/python3

# Terminal 3: Skill Server (code-analysis, LLM matching)
./skill-server/build/skillscale_skill_server \
  --topic TOPIC_CODE_ANALYSIS \
  --skills-dir ./skills/code-analysis \
  --matcher llm \
  --python .venv/bin/python3

# Terminal 4: Python Agent
source .venv/bin/activate
python3 agent/main.py
```

### Run with Docker Compose

```bash
docker compose up --build
```

Brings up the proxy, two skill servers (data-processing + code-analysis), and the agent.
Proxy ports `5444`/`5555`/`9100` are exposed on the host.

## Testing

### Integration Tests (34 tests, no live services required)

```bash
source .venv/bin/activate
python3 -m pytest tests/ -v
```

The test suite spins up an in-process ZeroMQ proxy and mock skill servers automatically.

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_01_proxy.py` | 5 | PubSub routing, topic filtering, JSON integrity, multi-subscriber |
| `test_02_agent_e2e.py` | 9 | Single/sequential/parallel requests, timeout handling, large payloads, request correlation |
| `test_03_skills.py` | 9 | Real subprocess execution of all 4 skill scripts, SKILL.md frontmatter parsing |
| `test_04_fault_tolerance.py` | 6 | Timeout enforcement, slow servers, stale future GC, rapid fire, crash recovery |

### Live E2E Test (requires running C++ services)

```bash
python3 test_e2e_live.py
```

### Concurrent Stress Test Suite (end-to-end latency)

Use the dedicated stress runner to measure throughput and latency percentiles
($p50$, $p95$, $p99$) under concurrent load.

```bash
source .venv/bin/activate

# Full pipeline: HTTP chat API -> routing -> ZMQ -> skill server -> response
python3 stress_test_e2e.py \
   --mode chat-api \
   --requests 200 \
   --concurrency 20 \
   --topic TOPIC_DATA_PROCESSING \
   --timeout 120

# Direct Docker network path: docker exec -> ZMQ -> skill server
python3 stress_test_e2e.py \
   --mode docker-exec \
   --requests 100 \
   --concurrency 10 \
   --topic TOPIC_CODE_ANALYSIS \
   --timeout 120

# Optional: export machine-readable summary
python3 stress_test_e2e.py --mode chat-api --requests 100 --concurrency 10 --json-out /tmp/skillscale_stress.json
```

Output includes:
- total duration and throughput (requests/sec)
- success rate and sample failures
- latency stats: `min`, `mean`, `p50`, `p90`, `p95`, `p99`, `max`

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

# Skill execution timeout (ms) — how long C++ skill server waits
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

  --topic <TOPIC>         ZeroMQ topic to subscribe to
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

All messages use ZeroMQ multipart frames with JSON payloads.

**Request** (Agent -> Skill Server):
```
Frame 0: "TOPIC_DATA_PROCESSING"          # topic prefix for routing
Frame 1: {                                 # JSON payload
  "request_id": "a1b2c3d4",
  "reply_to":   "AGENT_REPLY_9f8e7d6c",
  "intent":     "...",                     # Mode 1 or Mode 2
  "timestamp":  1739836800.123
}
```

**Response** (Skill Server -> Agent):
```
Frame 0: "AGENT_REPLY_9f8e7d6c"           # reply_to topic from request
Frame 1: {                                 # JSON payload
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
graph = sg.build_graph(task_based=True) # Mode 2: C++ server matches via LLM
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
