# SkillScale — Distributed Skill-as-a-Service Agent Infrastructure

A middleware SDK and distributed infrastructure for executing AI agent skills at scale.
C++ Skill Servers discover and execute skills using the
[OpenSkills](https://github.com/numman-ali/openskills) `AGENTS.md` format over a
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
3. **Skill matching** — The C++ skill server discovers skills from `AGENTS.md`
   (`<available_skills>` XML). By default, it calls a Python subprocess
   (`scripts/llm_match.py`) that uses an LLM to match the task to the best skill.
   If LLM matching fails, it falls back to keyword scoring automatically.
4. **Skill execution** — The matched skill's `scripts/run.py` is executed via POSIX
   `fork`/`exec`. Skills themselves call LLMs for intelligent analysis.
5. **Response** — Results are published back on the agent's ephemeral reply topic
   (`AGENT_REPLY_<id>`).

### OpenSkills Discovery Flow

```
Skill server starts
  → parses skills/<topic>/AGENTS.md
  → extracts <available_skills> XML
  → task arrives on ZMQ topic
  → LLM matching (default) or keyword scoring
  → openskills CLI loads SKILL.md (progressive disclosure)
  → scripts/run.py executed with task data on stdin
  → result published back via ZMQ
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
│   ├── openskills              # OpenSkills CLI (list/read/sync)
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
├── tests/                      # 34 integration & fault-tolerance tests
│   ├── conftest.py
│   ├── test_01_proxy.py
│   ├── test_02_agent_e2e.py
│   ├── test_03_skills.py
│   └── test_04_fault_tolerance.py
├── docker/                     # Multi-stage Dockerfiles
├── k8s/                        # Kubernetes manifests + CRDs + KEDA
├── .env.example                # LLM provider config template
├── pyproject.toml              # SDK packaging (pip install skillscale)
├── docker-compose.yml
├── AGENTS.md                   # Root-level OpenSkills agent instructions
├── setup.sh
├── launch_all.sh
├── run_e2e.sh
└── test_e2e_live.py
```

## Components

| Component | Language | Description |
|-----------|----------|-------------|
| **skillscale/** | Python 3.10+ | **Middleware SDK** — core ZMQ client, skill discovery, and framework adapters (LangChain, LangGraph, CrewAI) |
| **proxy/** | C++17 | XPUB/XSUB stateless message switch with Prometheus metrics on `:9100` |
| **skill-server/** | C++17 | Multi-threaded subscriber; parses `AGENTS.md` for OpenSkills discovery; matches tasks via LLM (default) or keyword scoring; executes skills via POSIX `fork`/`exec` with configurable timeouts |
| **scripts/** | Python + Shell | OpenSkills CLI (`openskills`), LLM skill matcher (`llm_match.py`), configurable prompt templates |
| **agent/** | Python 3.10+ | Standalone CLI agent built on the SDK; LLM-powered topic routing |
| **examples/** | Python | Working examples: direct client, LangChain agent, LangGraph graph |
| **skills/** | Markdown + Python | `AGENTS.md` discovery manifests, `SKILL.md` metadata, `scripts/run.py` LLM-powered executables, shared `llm_utils.py` |
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
| CMake ≥ 3.16 | `brew install cmake` | `apt install cmake` |
| libzmq | `brew install zeromq` | `apt install libzmq3-dev` |
| cppzmq | `brew install cppzmq` | `apt install libzmq3-dev` (headers included) |
| nlohmann-json | `brew install nlohmann-json` | `apt install nlohmann-json3-dev` |
| pkg-config | `brew install pkg-config` | `apt install pkg-config` |
| Python ≥ 3.10 | `brew install python` | `apt install python3 python3-venv` |

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

### Automated Setup (C++ build + Python deps)

```bash
chmod +x setup.sh && ./setup.sh
```

This installs Python packages, checks for C/C++ dependencies, and builds both C++ binaries.

### Manual Build

```bash
# Build proxy
cd proxy && mkdir -p build && cd build && cmake .. && make -j$(nproc) && cd ../..

# Build skill server
cd skill-server && mkdir -p build && cd build && cmake .. && make -j$(nproc) && cd ../..
```

Binaries are output to `proxy/build/skillscale_proxy` and
`skill-server/build/skillscale_skill_server`.

### Run Locally

```bash
# All-in-one launcher (starts proxy + 2 skill servers + runs E2E test)
chmod +x launch_all.sh && ./launch_all.sh
```

Or start each component manually in separate terminals:

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
# Start services, then:
python3 test_e2e_live.py
```

Sends real requests through the full stack:
Python Agent → C++ Proxy → C++ Skill Server → LLM match → subprocess → response.

## Configuration

### Environment Variables

| Variable | Default | Used By | Description |
|----------|---------|---------|-------------|
| `LLM_PROVIDER` | `azure` | all skills, agent, matcher | Active LLM provider (`azure`, `openai`, or `zhipu`) |
| `SKILLSCALE_MATCHER` | `llm` | skill-server | Skill matching strategy: `llm` (default) or `keyword` |
| `SKILLSCALE_PROMPT_FILE` | `""` | skill-server | Custom prompt template for LLM matching |
| `SKILLSCALE_PYTHON` | `python3` | skill-server | Python executable path for LLM subprocess |
| `SKILLSCALE_PROXY_XSUB` | `tcp://127.0.0.1:5444` | agent, skill-server | Proxy XSUB endpoint (publishers connect here) |
| `SKILLSCALE_PROXY_XPUB` | `tcp://127.0.0.1:5555` | agent, skill-server | Proxy XPUB endpoint (subscribers connect here) |
| `SKILLSCALE_TOPIC` | `TOPIC_DEFAULT` | skill-server | ZeroMQ topic to subscribe to |
| `SKILLSCALE_DESCRIPTION` | `""` | skill-server | Human-readable server description |
| `SKILLSCALE_SKILLS_DIR` | `./skills` | skill-server | Directory containing skill subdirectories |
| `SKILLSCALE_WORKERS` | `2` | skill-server | Number of worker threads |
| `SKILLSCALE_TIMEOUT` | `30000` / `30` | skill-server (ms) / agent (s) | Skill execution / request timeout |
| `SKILLSCALE_XSUB_PORT` | `5444` | proxy | XSUB bind port |
| `SKILLSCALE_XPUB_PORT` | `5555` | proxy | XPUB bind port |
| `SKILLSCALE_METRICS_PORT` | `9100` | proxy | Prometheus metrics port |

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

### Skill Matching Modes

| Mode | Flag | Description |
|------|------|-------------|
| **LLM** (default) | `--matcher llm` | Calls `scripts/llm_match.py` via Python subprocess. Uses the configured LLM provider to semantically match tasks to skills. Falls back to keyword on failure. |
| **Keyword** | `--matcher keyword` | Pure C++ keyword scoring against skill names and descriptions from `AGENTS.md`. No LLM required. |

The LLM matcher uses the prompt template at `scripts/prompts/skill_match.txt`, which can
be customized via `--prompt-file`.

## Message Protocol

All messages use ZeroMQ multipart frames with JSON payloads.

**Request** (Agent → Skill Server):
```
Frame 0: "TOPIC_DATA_PROCESSING"          # topic prefix for routing
Frame 1: {                                 # JSON payload
  "request_id": "a1b2c3d4",
  "reply_to":   "AGENT_REPLY_9f8e7d6c",
  "intent":     "...",                     # Mode 1 or Mode 2 (see below)
  "timestamp":  1739836800.123
}

# Mode 1 (explicit):    intent = '{"skill":"csv-analyzer","data":"name,age\\nAlice,30"}'
# Mode 2 (task-based):  intent = '{"task":"analyze this CSV data..."}'
# Mode 2 (plain text):  intent = 'analyze this CSV data: name,age\nAlice,30'
```

**Response** (Skill Server → Agent):
```
Frame 0: "AGENT_REPLY_9f8e7d6c"           # reply_to topic from request
Frame 1: {                                 # JSON payload
  "request_id": "a1b2c3d4",
  "status":     "success",
  "content":    "## CSV Analysis\n\n**Rows:** 1 | **Columns:** 2\n..."
}
```

## Kubernetes Deployment

```bash
kubectl apply -f k8s/
```

Manifests create:
- `skillscale` namespace
- Proxy Deployment + two ClusterIP Services (`:5444`, `:5555`)
- `SkillTopic` CustomResourceDefinition
- SkillTopic instances for `data-processing` and `code-analysis`
- KEDA ScaledObjects for event-driven autoscaling of skill servers
- Agent Deployment

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
tools = toolkit.get_tools()       # Mode 1: one tool per skill (explicit)
task_tools = toolkit.get_task_tools()  # Mode 2: one tool per topic (task-based)
all_tools = toolkit.get_all_tools()    # both modes combined

# Use with any LangChain agent
agent = create_react_agent(llm, all_tools, prompt)
```

### LangGraph

```python
from skillscale import SkillScaleClient
from skillscale.adapters.langgraph import SkillScaleGraph

client = SkillScaleClient()
await client.connect()

sg = SkillScaleGraph.from_skills_dir(client, "./skills")
graph = sg.build_graph(llm=my_llm)  # Mode 1: LLM picks the skill

# Or: Mode 2 — let the C++ server match skills via LLM
graph = sg.build_graph(task_based=True)
result = await graph.ainvoke({"input": "summarize this text..."})
```

### CrewAI

```python
from skillscale import SkillScaleClient
from skillscale.adapters.crewai import SkillScaleCrewTools

client = SkillScaleClient()
await client.connect()

crew_tools = SkillScaleCrewTools.from_skills_dir(client, "./skills")
tools = crew_tools.get_tools()          # Mode 1: explicit per-skill tools
task_tools = crew_tools.get_task_tools()  # Mode 2: task-based per-topic tools
all_tools = crew_tools.get_all_tools()    # both modes

agent = Agent(role="analyst", tools=all_tools, ...)
```

### Skill Discovery (progressive disclosure)

```python
from skillscale import SkillDiscovery

discovery = SkillDiscovery(
    skills_root="./skills",
    topic_descriptions={
        "TOPIC_DATA_PROCESSING": "Data processing — summarization, CSV analysis",
        "TOPIC_CODE_ANALYSIS": "Code analysis — complexity, metrics, static analysis",
    },
).scan()

print(discovery.metadata_summary())   # inject into LLM system prompt
print(discovery.list_topics())        # ["TOPIC_CODE_ANALYSIS", "TOPIC_DATA_PROCESSING"]

for tm in discovery.list_topic_metadata():
    print(f"{tm.topic}: {tm.description} — skills: {tm.skill_names()}")
```

## Adding a New Skill

1. Create a directory under the appropriate topic:
   ```
   skills/<category>/<skill-name>/
   ├── SKILL.md
   └── scripts/
       └── run.py
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
   Use `skills/llm_utils.py` for LLM calls:
   ```python
   from llm_utils import chat
   result = chat("You are an expert.", user_input)
   ```

4. Register the skill in the topic's `AGENTS.md` (add a `<skill>` entry to
   `<available_skills>`).

5. Update the root `AGENTS.md` with the new skill entry.

6. The skill server auto-discovers skills on startup by parsing `AGENTS.md`.

## License

MIT
