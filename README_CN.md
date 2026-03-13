# SkillScale — 分布式技能即服务 (Skill-as-a-Service) Agent 基础设施


> [English](README.md) | [简体中文](README_CN.md) | [繁體中文](README_TW.md) | [日本語](README_JP.md) | [Español](README_ES.md) | [Français](README_FR.md)


## 目录

- [解决什么问题](#解决什么问题)
- [核心架构](#核心架构)
- [请求完整流转过程](#请求完整流转过程)
- [两种调用粒度](#两种调用粒度)
- [协议能力详解](#协议能力详解)
- [内置技能一览](#内置技能一览)
- [快速启动](#快速启动)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [内部消息协议](#内部消息协议)
- [如何添加新技能](#如何添加新技能)
- [技术亮点](#技术亮点)

---

## 解决什么问题

当前 AI Agent 生态面临严重的**协议碎片化**问题：

| 客户端类型 | 使用的协议 | 典型产品 |
|-----------|-----------|---------|
| IDE / 编辑器插件 | MCP (Model Context Protocol) | Claude Desktop、Cursor、VS Code Copilot |
| 企业级 Agent 平台 | A2A (Agent-to-Agent) | Google Agent 平台、企业内部编排系统 |
| 自定义脚本 / 框架 | HTTP REST 或 SDK | LangChain、CrewAI、自研系统 |

**问题**：每种客户端说的"语言"不同，而后端的技能执行引擎又需要一套统一的接口。如果为每种协议单独写适配层，维护成本随协议数量爆炸式增长。

**SkillScale 的解法 — 三层架构**：

```
  协议层                  →  网关层                →  执行层
  (MCP / A2A 客户端)         (Rust Gateway,          (Kafka + Skill Server,
                              纯协议翻译)              技能发现 & LLM 路由)
```

**核心洞察**：Gateway 是一个纯粹的**协议翻译器** — 对外说 MCP 和 A2A，对内一律转成 Kafka 消息。这意味着：

- **新增协议** = 在 Gateway 里加一个 HTTP handler（不影响任何技能）
- **新增技能** = 在 `skills/` 下放一个文件夹然后重启（不影响任何协议）
- **水平扩展** = 多加几个 Skill Server 容器（Kafka Consumer Group 自动分发）

---

## 核心架构

```
┌───────────────────────────────────────────────────────────────┐
│  外部客户端                                                     │
│                                                               │
│  ┌─────────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │ Claude Desktop  │  │ Google Agent │  │ Python 脚本     │ │
│  │ Cursor / IDE    │  │ 企业平台      │  │ LangChain 等    │ │
│  │ (MCP SSE)       │  │ (A2A REST)   │  │ (MCP 或 A2A)    │ │
│  └───────┬─────────┘  └──────┬───────┘  └───────┬─────────┘ │
└──────────┼───────────────────┼──────────────────┼────────────┘
           │ :8086/mcp         │ :8085            │
┌──────────▼───────────────────▼──────────────────▼────────────┐
│                   Rust Gateway (axum + rmcp)                  │
│                                                               │
│  MCP Streamable HTTP 服务 ◄──► A2A REST 服务                   │
│  (端口 8086)                    (端口 8085)                    │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Kafka Producer / Consumer (rdkafka)                     │ │
│  │  - 发送到 TOPIC_<分类名>                                   │ │
│  │  - 从 REPLY_<request_id> 接收回复                          │ │
│  └─────────────────────┬───────────────────────────────────┘ │
└────────────────────────┼─────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Redpanda (Kafka)   │
              │  端口 9092           │
              └──────┬─────────┬────┘
                     │         │
      ┌──────────────▼──┐  ┌──▼──────────────────┐
      │  Skill Server   │  │  Skill Server        │
      │  (Rust 二进制)    │  │  (Rust 二进制)        │
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

### 各组件职责

| 组件 | 实现语言 | 端口 | 职责说明 |
|------|---------|------|---------|
| **Gateway（网关）** | Rust (axum + rmcp) | 8085, 8086 | 协议翻译器：对外提供 A2A REST 和 MCP SSE 两种接口，对内统一转为 Kafka 消息 |
| **Redpanda** | — | 9092 | Kafka 兼容的消息代理，负责 Topic 路由和消息持久化 |
| **Skill Server（技能服务器）** | Rust | — | 每个技能分类一个实例，消费 Kafka 消息，通过 AGENTS.md + LLM 匹配最佳技能并执行 |
| **Skills（技能）** | 任意语言 | — | 自包含的执行单元，遵循 stdin→stdout 契约，语言无关 |

---

## 请求完整流转过程

以一个 MCP 客户端调用 `code-analysis` 技能为例：

```
 客户端                     Rust Gateway              Redpanda            Skill Server
   │                          │                        │                     │
   │── MCP call_tool ────────▶│                        │                     │
   │   或 A2A POST            │                        │                     │
   │                          │── Kafka Produce ──────▶│                     │
   │                          │   topic: TOPIC_CODE_   │                     │
   │                          │   ANALYSIS             │                     │
   │                          │   reply_to: REPLY_xxx  │                     │
   │                          │                        │── Kafka Consume ──▶│
   │                          │                        │                     │── 解析 AGENTS.md
   │                          │                        │                     │── LLM 智能匹配技能
   │                          │                        │                     │── 执行技能 (stdin→stdout)
   │                          │                        │                     │── LLM 审查结果（可选）
   │                          │                        │◀── Kafka Produce ──│
   │                          │                        │   topic: REPLY_xxx  │
   │                          │◀── Kafka Consume ──────│                     │
   │◀── MCP 结果 ────────────│                        │                     │
   │    或 A2A 响应           │                        │                     │
```

### 分步详解

1. **协议入站** — 客户端通过 MCP（Streamable HTTP, 端口 8086）或 A2A（REST, 端口 8085）发送请求。Rust Gateway 解析协议特定的包装格式，提取核心意图。

2. **Topic 路由** — Gateway 根据请求内容映射到对应的 Kafka Topic：
   - MCP `agent__code-analysis` → Topic `TOPIC_CODE_ANALYSIS`（粗粒度，由 Skill Server 自行选择子技能）
   - MCP `code-analysis__dead-code-detector` → Topic `TOPIC_CODE_ANALYSIS` + 携带技能名 hint（细粒度，直接执行）
   - A2A `POST /v1/agents/code-analysis/converse` → Topic `TOPIC_CODE_ANALYSIS`（粗粒度）

3. **Kafka 发送** — Gateway 发布一条 JSON 消息，包含唯一的 `request_id` 和 `reply_to` Topic 名称。随后订阅该 reply topic，异步等待结果（默认超时 600 秒）。

4. **Skill Server 消费** — 每个技能分类对应一个 Rust Skill Server 实例，它从自己的 Topic 消费消息。收到后解析 `AGENTS.md` 中的技能清单，使用 **LLM 意图匹配**（或关键词降级匹配）选出最佳技能。

5. **技能执行** — 匹配到的技能以子进程方式运行（stdin = 输入，stdout = 结果）。技能可以用**任何编程语言**编写 — Python、Go、Rust、Node.js、Shell 脚本均可 — 唯一要求是遵循 stdin→stdout 契约。

6. **结果回传** — 执行结果发布到 `reply_to` Topic。Gateway 消费后将结果格式化为 MCP 或 A2A 协议的响应，返回给客户端。

---

## 两种调用粒度

SkillScale 支持两种调用方式，适应不同场景：

### 粗粒度（Coarse-grained）— "我只知道大方向"

调用方只指定技能分类（如"代码分析"），具体执行哪个子技能由系统自动决定。

- **MCP 工具名**：`agent__code-analysis`
- **A2A 端点**：`POST /v1/agents/code-analysis/converse`
- **路由逻辑**：Skill Server 读取 `AGENTS.md`，调用 LLM 根据用户输入自动匹配最合适的子技能
- **适用场景**：IDE 集成、通用 Agent 委托、用户不清楚该用哪个具体技能

### 细粒度（Fine-grained）— "我明确知道要什么"

调用方直接指定要执行的具体技能，跳过 LLM 路由。

- **MCP 工具名**：`code-analysis__dead-code-detector`
- **A2A 端点**：不适用（A2A 只支持粗粒度）
- **路由逻辑**：直接执行指定的技能，无需 LLM 参与
- **适用场景**：编程控制、测试、已知明确需求

| 粒度 | MCP 工具名 | A2A 端点 | 路由方式 |
|------|-----------|---------|---------|
| **粗粒度** | `agent__code-analysis` | `POST /v1/agents/code-analysis/converse` | AGENTS.md + LLM 自动选择 |
| **细粒度** | `code-analysis__dead-code-detector` | 不适用 | 直接执行，无 LLM |

> **设计决策**：A2A 协议只支持粗粒度（agent 级别路由）；MCP 同时支持两种模式。

---

## 协议能力详解

### A2A（Agent-to-Agent 协议）

符合 [Google A2A Protocol](https://github.com/google/a2a-protocol) 标准。

```http
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

**特点**：
- 仅支持**粗粒度**调用 — URL 路径中的 `agent_id` 决定 Kafka Topic
- 请求体中不包含技能元数据 — 由 Skill Server 内部自动决定执行哪个技能
- 响应格式遵循标准 A2A TaskResult 结构

### MCP（Model Context Protocol）

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

# 连接 MCP SSE 端点
async with streamablehttp_client("http://localhost:8086/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()

        # 列出所有可用工具（自动发现）
        tools = await session.list_tools()

        # 粗粒度：Agent 通过 AGENTS.md 自动选择技能
        result = await session.call_tool("agent__code-analysis", {"input": code})

        # 细粒度：直接调用指定技能
        result = await session.call_tool("code-analysis__dead-code-detector", {"input": code})
```

**特点**：
- 同时支持**粗粒度和细粒度** — `agent__<分类名>` 或 `<分类名>__<技能名>`
- 通过 **Streamable HTTP (SSE)** 提供服务（端口 8086）— 无需启动独立二进制
- 启动时自动扫描 `skills/` 目录，发现并注册所有可用工具
- `list_tools()` 暴露完整技能目录，适合 IDE 和 Agent 集成

---

## 内置技能一览

| 技能名 | 所属分类 | 功能说明 |
|-------|---------|---------|
| `code-complexity` | `code-analysis` | 基于 Python AST 分析代码复杂度（圈复杂度、嵌套深度、函数长度）+ LLM 生成重构建议 |
| `dead-code-detector` | `code-analysis` | 基于 AST 的死代码检测（未使用的导入、空函数、不可达代码）+ LLM 生成清理建议 |
| `text-summarizer` | `data-processing` | LLM 驱动的文本摘要，附带词数/句数统计 |
| `csv-analyzer` | `data-processing` | CSV 数据的统计分析（列类型、分布、缺失值）+ LLM 生成数据洞察 |

> **注意**：这些是演示用的示例技能。SkillScale 本身是语言无关的中间件 — 你可以用任何语言编写自己的技能。

---

## 快速启动

### 前置依赖

| 依赖 | macOS 安装 | Ubuntu/Debian 安装 |
|------|-----------|-------------------|
| Docker & Docker Compose | `brew install docker` | `apt install docker.io docker-compose-plugin` |
| Python >= 3.10 | `brew install python` | `apt install python3 python3-venv` |

> Rust 编译在 Docker 内部完成 — 本地**不需要**安装 Rust 工具链。

### 一键启动

```bash
./run_all.sh
```

这条命令会自动完成以下所有步骤：
1. 创建 `.venv` 虚拟环境并安装 Python 依赖
2. 运行 `build.sh` 生成 `docker-compose.yml`、构建 Docker 镜像、启动所有服务
3. 等待 Gateway 就绪（端口 8085 + 8086 可达）
4. 运行 `demo_a2a_client.py` 和 `demo_mcp_client.py` 两个演示脚本验证系统

启动成功后你会看到：

```
==========================================
    SkillScale System Ready!
==========================================
  • A2A 网关:       http://localhost:8085
  • MCP 服务:       http://localhost:8086/mcp
  • 管理控制台:      http://localhost:8080
  • Kafka Broker:   localhost:9092
==========================================
```

### 单独运行演示

```bash
# A2A 协议演示 — 粗粒度 Agent 调用
python3 examples/demo_a2a_client.py

# MCP 协议演示 — 粗粒度 + 细粒度均包含
python3 examples/demo_mcp_client.py
```

### 停止服务

```bash
docker compose down -v    # -v 清除 Kafka 数据卷（干净状态）
```

---

## 项目结构

```
SkillScale/
├── skillscale-rs/              # Rust 工作空间（Docker 内编译）
│   ├── gateway/src/            # Axum HTTP 服务器（A2A + MCP）
│   │   ├── main.rs             # 入口：在 8086 启动 MCP，在 8085 启动 A2A
│   │   ├── mcp_server.rs       # 通过 rmcp StreamableHttpService 提供 MCP SSE 服务
│   │   └── skill_discovery.rs  # 扫描 skills/ 目录，解析 AGENTS.md，注册 MCP 工具
│   ├── skill-server/src/       # Kafka 消费者 + 技能执行器
│   │   └── main.rs             # 消费 Topic 消息，通过 AGENTS.md 匹配技能并执行
│   └── common/src/             # 共享的 Kafka 消息类型定义
├── skills/                     # 技能定义（语言无关）
│   ├── code-analysis/          # 技能分类：代码分析
│   │   ├── AGENTS.md           # 技能发现清单（XML 标签格式）
│   │   └── .claude/skills/
│   │       ├── code-complexity/    # SKILL.md + 可执行脚本
│   │       └── dead-code-detector/ # SKILL.md + 可执行脚本
│   └── data-processing/        # 技能分类：数据处理
│       ├── AGENTS.md
│       └── .claude/skills/
│           ├── text-summarizer/    # SKILL.md + 可执行脚本
│           └── csv-analyzer/       # SKILL.md + 可执行脚本
├── examples/                   # 即开即用的演示脚本
│   ├── demo_a2a_client.py      # A2A 协议演示（粗粒度）
│   └── demo_mcp_client.py      # MCP 协议演示（粗粒度 + 细粒度）
├── docker/                     # 多阶段 Dockerfile
│   └── Dockerfile.rust         # 构建 Gateway + Skill Server + 打包技能
├── build.sh                    # Docker 构建 & 启动（自动生成 docker-compose.yml）
├── run_all.sh                  # 完整引导：虚拟环境 + 构建 + 启动 + 验证
└── .env                        # API 密钥与配置
```

---

## 配置说明

### LLM 供应商（仅示例技能需要）

内置的示例技能在执行过程中会调用 LLM。在 `.env` 中配置密钥：

```bash
cp .env.example .env   # 然后填入你的 API 密钥
```

| 供应商 | 环境变量 | 示例模型 |
|-------|---------|---------|
| `azure` | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_MODEL` | gpt-4o |
| `openai` | `OPENAI_API_KEY`, `OPENAI_API_BASE`, `OPENAI_MODEL` | DeepSeek-V3 |
| `zhipu` | `ZHIPU_API_KEY`, `ZHIPU_MODEL` | GLM-4.7-FlashX |

在 `.env` 中设置 `LLM_PROVIDER=azure|openai|zhipu` 选择激活的供应商。

> **重要提示**：LLM 密钥仅供示例技能使用。SkillScale 中间件本身与 LLM 无关 — 你的自定义技能可以使用任何后端，或者完全不用 LLM。

### Gateway 超时设置

统一超时时间（默认 600 秒），通过环境变量配置：

```bash
SKILLSCALE_GATEWAY_TIMEOUT=600.0   # 秒，Gateway 和演示客户端共用
```

---

## 内部消息协议

系统内部一律使用 Kafka Topic 路由 + JSON 格式的消息通信。

**请求消息**（Gateway → Skill Server）：
```json
{
  "request_id": "a1b2c3d4",
  "reply_to":   "REPLY_a1b2c3d4",
  "intent":     "def foo(): pass",
  "timestamp":  1739836800.123
}
```

**响应消息**（Skill Server → Gateway）：
```json
{
  "request_id": "a1b2c3d4",
  "status":     "success",
  "content":    "## 死代码报告\n..."
}
```

**关键设计**：
- 每个请求携带唯一 `request_id`（UUID），用于关联请求与响应
- `reply_to` 字段指定 Gateway 监听回复的 Topic 名称
- Skill Server 处理完成后，将结果发送到 `reply_to` Topic
- Gateway 通过 `request_id` 从 `pending_requests` 映射表中找到对应的等待者，完成响应回传

---

## 如何添加新技能

### 第一步：创建技能目录

在 `skills/<分类名>/` 下创建技能定义，入口脚本可以用任意语言：

```
skills/<分类名>/.claude/skills/<技能名>/
├── SKILL.md          # 元数据（名称、描述、使用说明）
└── scripts/
    └── run.py        # 入口脚本：从 stdin 读取输入，向 stdout 输出结果
    # 也可以是 run.sh、run.js、甚至编译好的二进制 — 任何遵循 stdin→stdout 的可执行文件
```

### 第二步：更新技能清单

编辑 `skills/<分类名>/AGENTS.md`，在 `<available_skills>` 部分添加新技能的描述。这是 Skill Server 用来做技能匹配的清单。

### 第三步：重建并启动

```bash
docker compose build && docker compose up -d
```

### 第四步：自动发现

Gateway 在下次启动时会自动扫描 `skills/` 目录，将新技能注册为 MCP 工具。无需修改 Gateway 代码。

> **语言无关**：技能可以用 Python、Go、Rust、Node.js、Shell 或任何语言编写。唯一的契约是：从 stdin 读输入，向 stdout 写输出。中间件不关心你的技能用什么语言实现。

### 完整示例

假设要添加一个名为 `json-validator` 的新技能到 `data-processing` 分类：

```bash
# 1. 创建目录
mkdir -p skills/data-processing/.claude/skills/json-validator/scripts

# 2. 创建 SKILL.md
cat > skills/data-processing/.claude/skills/json-validator/SKILL.md << 'EOF'
# JSON Validator
验证 JSON 数据的格式正确性，报告语法错误位置。
EOF

# 3. 创建入口脚本（Python 示例）
cat > skills/data-processing/.claude/skills/json-validator/scripts/run.py << 'EOF'
import sys, json
data = sys.stdin.read()
try:
    json.loads(data)
    print("✅ JSON 格式正确")
except json.JSONDecodeError as e:
    print(f"❌ JSON 格式错误: 第 {e.lineno} 行, 第 {e.colno} 列 — {e.msg}")
EOF

# 4. 在 AGENTS.md 中注册
# 编辑 skills/data-processing/AGENTS.md，添加 json-validator 到 <available_skills>

# 5. 重建
docker compose build && docker compose up -d
```

---

## 技术亮点

### 为什么选择 Kafka/Redpanda 而非直连？

| 特性 | Kafka（当前方案） | 直连（ZeroMQ 等） |
|------|-----------------|-----------------|
| 负载均衡 | ✅ Consumer Group 天然支持 | ❌ 需要额外负载均衡器 |
| 消息持久化 | ✅ 可重放 | ❌ 丢即丢 |
| 水平扩展 | ✅ 加实例即可，无需改配置 | ❌ 需要服务发现 |
| 延迟 | ⚠️ 多 10-50ms | ✅ 亚毫秒 |
| 运维复杂度 | ⚠️ 需管理 Broker | ✅ 零依赖 |

> 系统早期使用 ZeroMQ 直连，后升级到 Kafka 以获得更好的扩展性和可靠性。

### 为什么 Gateway 用 Rust？

- **性能**：axum + tokio 异步运行时，万级并发连接零压力
- **内存安全**：零成本抽象，无 GC 停顿，生产环境稳如磐石
- **MCP 原生支持**：使用 [rmcp](https://github.com/modelcontextprotocol/rust-sdk) 官方 Rust SDK，与 MCP 规范完全兼容
- **二进制部署**：编译为单一静态二进制，Docker 镜像极小

### 安全隔离

Skill Server 容器采用严格的安全策略：

```yaml
read_only: true                              # 只读文件系统
tmpfs: [/tmp, /run, /var/tmp, /root/.cache]  # 临时文件走内存
security_opt: [no-new-privileges:true]       # 禁止提权
```

每次技能执行都在干净的临时环境中运行，防止跨请求状态污染。

### 零配置技能注册

添加新技能只需要放文件夹 + 重启，不需要修改任何代码：

1. `build.sh` 自动扫描 `skills/` 目录，为每个分类生成 `docker-compose.yml` 中的 Service 定义
2. Gateway 启动时自动扫描 `skills/*/AGENTS.md`，解析 XML 标签，注册为 MCP 工具
3. 新技能立即对所有 MCP 和 A2A 客户端可用

---

## License

MIT