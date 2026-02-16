# SkillScale — Distributed Skill-as-a-Service Agent Infrastructure

A massively scalable, ZeroMQ-based distributed system where specialized C++ Skill Servers
execute [OpenSkills](https://github.com/anthropics/openskills) on behalf of a lightweight
Python front-end agent orchestrator. Skills are defined as portable `SKILL.md` files with
YAML frontmatter and are executed as isolated subprocesses — no mocks, no stubs.

## Architecture

```
                           ┌─────────────────────┐
                           │   ZeroMQ XPUB/XSUB  │
                           │   Proxy (C++)        │
                           │                      │
 ┌──────────────┐   PUB    │  XSUB :5444          │   SUB    ┌────────────────────────┐
 │  Python       │────────▶│          ↓            │────────▶│  C++ Skill Server       │
 │  Front-End    │         │     forward msgs      │         │  Topic: DATA_PROCESSING  │
 │  Agent        │   SUB   │          ↓            │   PUB   │  ├─ text-summarizer      │
 │               │◀────────│  XPUB :5555           │◀────────│  └─ csv-analyzer         │
 │  Tools:       │         │                      │         └────────────────────────┘
 │  • publish()  │         │  Metrics :9100        │
 │  • respond()  │         └─────────────────────┘         ┌────────────────────────┐
 └──────────────┘                                          │  C++ Skill Server       │
                                                           │  Topic: CODE_ANALYSIS   │
                                                           │  └─ code-complexity     │
                                                           └────────────────────────┘
```

**Data flow:** The agent publishes a JSON intent to a topic (e.g. `TOPIC_DATA_PROCESSING`).
The proxy forwards it to the matching skill server. The skill server executes the
appropriate skill script as a subprocess and publishes the result back on the agent's
ephemeral reply topic (`AGENT_REPLY_<id>`). Everything is fully asynchronous and
bidirectional.

## Project Structure

```
SkillScale/
├── proxy/                  # C++ XPUB/XSUB proxy (stateless message switch)
│   ├── main.cpp
│   └── CMakeLists.txt
├── skill-server/           # C++ skill server (subscriber + subprocess executor)
│   ├── main.cpp            # CLI arg parsing, worker thread pool, ZMQ sockets
│   ├── skill_loader.cpp/h  # Parses SKILL.md YAML frontmatter, discovers skills
│   ├── skill_executor.cpp/h # POSIX fork/exec with timeout enforcement
│   ├── message_handler.cpp/h # JSON request/response envelope serialization
│   └── CMakeLists.txt
├── agent/                  # Python async orchestrator
│   ├── main.py             # SkillScaleAgent with publish() / respond() tools
│   └── requirements.txt
├── skills/                 # Portable skill definitions (SKILL.md + scripts/)
│   ├── data-processing/
│   │   ├── text-summarizer/
│   │   └── csv-analyzer/
│   └── code-analysis/
│       └── code-complexity/
├── tests/                  # 34 integration & fault-tolerance tests (pytest)
│   ├── conftest.py         # Real ZMQ proxy fixture + MockSkillServer
│   ├── test_01_proxy.py    # PubSub routing, topic filtering, multi-subscriber
│   ├── test_02_agent_e2e.py # Single/parallel/sequential requests, timeouts
│   ├── test_03_skills.py   # Real subprocess execution of all skill scripts
│   └── test_04_fault_tolerance.py # Crash recovery, stale GC, rapid fire
├── docker/                 # Multi-stage Dockerfiles
│   ├── Dockerfile.proxy
│   ├── Dockerfile.skill-server
│   └── Dockerfile.agent
├── k8s/                    # Kubernetes manifests
│   ├── 00-namespace.yaml
│   ├── 01-proxy.yaml       # Deployment + ClusterIP Services for proxy
│   ├── 02-crd-skilltopic.yaml # SkillTopic CustomResourceDefinition
│   ├── 03-skilltopic-instances.yaml
│   ├── 04-keda-scaledobjects.yaml # Event-driven autoscaling via KEDA
│   └── 05-agent.yaml
├── docker-compose.yml      # Local multi-service dev environment
├── setup.sh                # One-shot bootstrap (deps + build)
├── launch_all.sh           # Start all services + run E2E test
├── run_e2e.sh              # Full-stack E2E runner (proxy + server + agent)
└── test_e2e_live.py        # Live smoke test against running C++ services
```

## Components

| Component | Language | Description |
|-----------|----------|-------------|
| **proxy/** | C++17 | XPUB/XSUB stateless message switch with Prometheus metrics on `:9100` |
| **skill-server/** | C++17 | Multi-threaded subscriber; dispatches intents to worker threads; executes skills via POSIX `fork`/`exec` with configurable timeouts |
| **agent/** | Python 3.10+ | Async orchestrator using `pyzmq` + `asyncio`; exposes `publish(topic, intent)` and `respond(content)` tools |
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

### Automated Setup

```bash
chmod +x setup.sh && ./setup.sh
```

This installs Python packages (`pyzmq`, `pyyaml`, `pytest`, `pytest-asyncio`,
`pytest-timeout`), checks for C/C++ dependencies, and builds both C++ binaries.

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
