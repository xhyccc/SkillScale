# SkillScale — Distributed Skill-as-a-Service Agent Infrastructure

A middleware SDK and distributed infrastructure for executing AI agent skills at scale.
Specialized C++ Skill Servers execute [OpenSkills](https://github.com/anthropics/openskills)
over a ZeroMQ pub/sub bus. Any agent framework — **LangChain, LangGraph, CrewAI**, or your
own — plugs into the middleware through thin adapters. No mocks, no stubs.

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
│  │ (async ZMQ)    │  │ LC/LG/Crew │  │ (SKILL.md scan)  │  │
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
 │  ├─ text-summarizer       │    │  └─ code-complexity      │
 │  └─ csv-analyzer          │    └──────────────────────────┘
 └───────────────────────────┘
```

**How it works:** Your agent framework invokes a SkillScale tool (backed by the SDK client).
The client publishes a JSON intent to a ZMQ topic (e.g. `TOPIC_DATA_PROCESSING`). The
stateless XPUB/XSUB proxy forwards it to the matching C++ skill server. The skill server
executes the skill script as a subprocess and publishes the result back on the agent's
ephemeral reply topic (`AGENT_REPLY_<id>`). Everything is fully asynchronous and
bidirectional.

## Project Structure

```
SkillScale/
├── skillscale/                 # Python SDK (the middleware)
│   ├── __init__.py             # Public API: SkillScaleClient, SkillDiscovery
│   ├── client.py               # Core async ZMQ pub/sub client
│   ├── discovery.py            # SKILL.md scanner & metadata registry
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
├── skill-server/               # C++ skill server
│   ├── main.cpp                # Worker thread pool, ZMQ sockets
│   ├── skill_loader.cpp/h      # SKILL.md YAML parser
│   ├── skill_executor.cpp/h    # POSIX fork/exec with timeout
│   ├── message_handler.cpp/h   # JSON envelope serialization
│   └── CMakeLists.txt
├── agent/                      # Standalone CLI agent (uses SDK)
│   ├── main.py
│   └── requirements.txt
├── skills/                     # Portable skill definitions
│   ├── data-processing/
│   │   ├── text-summarizer/
│   │   └── csv-analyzer/
│   └── code-analysis/
│       └── code-complexity/
├── tests/                      # 34 integration & fault-tolerance tests
│   ├── conftest.py
│   ├── test_01_proxy.py
│   ├── test_02_agent_e2e.py
│   ├── test_03_skills.py
│   └── test_04_fault_tolerance.py
├── docker/                     # Multi-stage Dockerfiles
├── k8s/                        # Kubernetes manifests + CRDs + KEDA
├── pyproject.toml              # SDK packaging (pip install skillscale)
├── docker-compose.yml
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
| **skill-server/** | C++17 | Multi-threaded subscriber; dispatches intents to worker threads; executes skills via POSIX `fork`/`exec` with configurable timeouts |
| **agent/** | Python 3.10+ | Standalone CLI agent built on the SDK |
| **examples/** | Python | Working examples: direct client, LangChain agent, LangGraph graph |
| **skills/** | Markdown + Python | `SKILL.md` files with YAML frontmatter and `scripts/run.py` executables |
| **tests/** | Python (pytest) | 34 tests covering proxy routing, agent E2E, skill execution, and fault tolerance |
| **k8s/** | YAML | Namespace, Deployments, Services, SkillTopic CRD, KEDA ScaledObjects |

## Available Skills

| Skill | Topic | Description |
|-------|-------|-------------|
| `text-summarizer` | `TOPIC_DATA_PROCESSING` | Extractive summarization using word-frequency scoring (pure Python, no LLM) |
| `csv-analyzer` | `TOPIC_DATA_PROCESSING` | Statistical analysis of CSV data — counts, min/max/mean/median per column |
| `code-complexity` | `TOPIC_CODE_ANALYSIS` | Python AST-based static analysis — cyclomatic complexity, nesting depth, function lengths |

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

### Run Locally (3 terminals)

```bash
# Terminal 1: XPUB/XSUB Proxy
./proxy/build/skillscale_proxy

# Terminal 2: Skill Server (data-processing topic)
./skill-server/build/skillscale_skill_server \
  --topic TOPIC_DATA_PROCESSING \
  --skills-dir ./skills/data-processing

# Terminal 3: Python Agent
source .venv/bin/activate   # if using a venv
python3 agent/main.py
```

Or use the all-in-one launcher:

```bash
chmod +x launch_all.sh && ./launch_all.sh
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
python3 -m pytest tests/ -v --timeout=30
```

The test suite spins up an in-process ZeroMQ proxy and mock skill servers automatically.

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_01_proxy.py` | 5 | PubSub routing, topic filtering, JSON integrity, multi-subscriber |
| `test_02_agent_e2e.py` | 9 | Single/sequential/parallel requests, timeout handling, large payloads, request correlation |
| `test_03_skills.py` | 8 | Real subprocess execution of all skill scripts, SKILL.md frontmatter parsing |
| `test_04_fault_tolerance.py` | 6 | Timeout enforcement, slow servers, stale future GC, rapid fire, crash recovery |

### Live E2E Test (requires running C++ services)

```bash
# Start services, then:
python3 test_e2e_live.py
```

Sends real requests through the full stack:
Python Agent → C++ Proxy → C++ Skill Server → subprocess → response.

## Configuration

### Environment Variables

| Variable | Default | Used By | Description |
|----------|---------|---------|-------------|
| `SKILLSCALE_PROXY_XSUB` | `tcp://127.0.0.1:5444` | agent, skill-server | Proxy XSUB endpoint (publishers connect here) |
| `SKILLSCALE_PROXY_XPUB` | `tcp://127.0.0.1:5555` | agent, skill-server | Proxy XPUB endpoint (subscribers connect here) |
| `SKILLSCALE_TOPIC` | `TOPIC_DEFAULT` | skill-server | ZeroMQ topic to subscribe to |
| `SKILLSCALE_SKILLS_DIR` | `./skills` | skill-server | Directory containing skill subdirectories |
| `SKILLSCALE_WORKERS` | `4` | skill-server | Number of worker threads |
| `SKILLSCALE_TIMEOUT` | `30000` / `30` | skill-server (ms) / agent (s) | Skill execution / request timeout |
| `SKILLSCALE_XSUB_PORT` | `5444` | proxy | XSUB bind port |
| `SKILLSCALE_XPUB_PORT` | `5555` | proxy | XPUB bind port |
| `SKILLSCALE_METRICS_PORT` | `9100` | proxy | Prometheus metrics port |

### Skill Server CLI Arguments

```
./skillscale_skill_server [OPTIONS]

  --topic <TOPIC>         ZeroMQ topic to subscribe to
  --skills-dir <DIR>      Path to skills directory
  --proxy-xpub <ADDR>     Proxy XPUB address (default: tcp://127.0.0.1:5555)
  --proxy-xsub <ADDR>     Proxy XSUB address (default: tcp://127.0.0.1:5444)
  --workers <N>           Number of worker threads (default: 4)
  --timeout <MS>          Skill execution timeout in ms (default: 30000)
```

## Message Protocol

All messages use ZeroMQ multipart frames with JSON payloads.

**Request** (Agent → Skill Server):
```
Frame 0: "TOPIC_DATA_PROCESSING"          # topic prefix for routing
Frame 1: {                                 # JSON payload
  "request_id": "a1b2c3d4",
  "reply_to":   "AGENT_REPLY_9f8e7d6c",
  "intent":     "{\"skill\":\"csv-analyzer\",\"data\":\"name,age\\nAlice,30\"}",
  "timestamp":  1739836800.123
}
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
        intent = json.dumps({"skill": "text-summarizer", "data": "Some long text..."})
        result = await client.invoke("TOPIC_DATA_PROCESSING", intent)
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
tools = toolkit.get_tools()  # one LangChain Tool per skill

# Use with any LangChain agent
agent = create_react_agent(llm, tools, prompt)
```

### LangGraph

```python
from skillscale import SkillScaleClient
from skillscale.adapters.langgraph import SkillScaleGraph

client = SkillScaleClient()
await client.connect()

sg = SkillScaleGraph.from_skills_dir(client, "./skills")
graph = sg.build_graph(llm=my_llm)  # or llm=None for keyword routing
result = await graph.ainvoke({"input": "summarize this text..."})
```

### CrewAI

```python
from skillscale import SkillScaleClient
from skillscale.adapters.crewai import SkillScaleCrewTools

client = SkillScaleClient()
await client.connect()

crew_tools = SkillScaleCrewTools.from_skills_dir(client, "./skills")
tools = crew_tools.get_tools()  # one CrewAI Tool per skill

agent = Agent(role="analyst", tools=tools, ...)
```

### Skill Discovery (progressive disclosure)

```python
from skillscale import SkillDiscovery

discovery = SkillDiscovery(skills_root="./skills").scan()
print(discovery.metadata_summary())   # inject into LLM system prompt
print(discovery.list_topics())        # ["TOPIC_CODE_ANALYSIS", "TOPIC_DATA_PROCESSING"]
```

## Adding a New Skill

1. Create a directory under the appropriate topic:
   ```
   skills/<category>/<skill-name>/
   ├── SKILL.md
   └── scripts/
       └── run.py    # or run.sh
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

3. Create `scripts/run.py` — reads from `SKILLSCALE_INTENT` env var or stdin,
   writes markdown to stdout.

4. The skill server auto-discovers skills on startup by scanning the `--skills-dir` directory.

## License

MIT
