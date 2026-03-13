# SkillScale 架构分析

> 基于 Clean Architecture 与 DDD 原则的系统架构评审

---

## 1. 系统全景

SkillScale 是一个**分布式 AI 技能执行平台**，将 LLM 能力封装为可独立部署、水平扩展的「技能单元」，通过消息代理（Redpanda/Kafka）解耦调用方与执行方。

```
┌─────────────────── 客户端层 ───────────────────┐
│  MCP Client (SSE)     A2A Client (REST)        │
│  Python SDK (Kafka)   LangChain/CrewAI Adapter │
└────────────┬──────────────────┬─────────────────┘
             │                  │
┌────────────▼──────────────────▼─────────────────┐
│              Rust Gateway (axum)                │
│  ┌──────────────┐  ┌────────────────────┐       │
│  │ A2A HTTP     │  │ MCP SSE Server     │       │
│  │ :8085        │  │ :8086 (rmcp)       │       │
│  └──────┬───────┘  └──────┬─────────────┘       │
│         │   skill_discovery (AGENTS.md 扫描)     │
│         └──────────┬───────┘                    │
└────────────────────┼────────────────────────────┘
                     │ Kafka Produce
┌────────────────────▼────────────────────────────┐
│           Redpanda (Kafka-compatible)            │
│  TOPIC_CODE_ANALYSIS  TOPIC_DATA_PROCESSING ...  │
│  gateway-replies-<uuid>                          │
└────────────────────┬────────────────────────────┘
                     │ Kafka Consume
┌────────────────────▼────────────────────────────┐
│         Skill Server (Rust, per-topic)           │
│  ┌──────────────────────────────────────┐       │
│  │  Kafka Consumer → Parse → Dispatch   │       │
│  │  opencode-exec (bash) → OpenCode CLI │       │
│  │  → AGENTS.md → skill script (stdin→stdout) │  │
│  └──────────────┬───────────────────────┘       │
│                 │ Kafka Produce (reply)          │
└─────────────────┼───────────────────────────────┘
                  ▼
┌─────────────────────────────────────────────────┐
│          Skill Scripts (language-agnostic)        │
│  Any executable: Python, Go, Rust, Node, shell   │
│  Contract: stdin→stdout, pure function execution   │
└─────────────────────────────────────────────────┘
```

---

## 2. 层次结构分析 (Clean Architecture 视角)

### 2.1 依赖方向

Clean Architecture 要求依赖**由外向内**：Framework → Interface Adapters → Use Cases → Domain。

当前系统的实际依赖方向：

```
外层 (Framework)
  ├── Docker / docker-compose.yml / build.sh
  ├── axum (HTTP framework)
  ├── rmcp (MCP SDK)
  ├── rdkafka (Kafka driver)
  │
中层 (Interface Adapters)
  ├── gateway/main.rs        — 协议适配 (A2A ↔ Kafka)
  ├── gateway/mcp_server.rs  — 协议适配 (MCP ↔ Kafka)
  ├── skill_discovery.rs     — 文件系统 → 内存模型
  │
内层 (Domain / Use Cases)
  ├── common/lib.rs          — 领域模型 (SendTaskParams, Message, Part, Role)
  ├── scripts/opencode-exec  — 技能执行用例
  └── skills/*/              — 具体技能实现（语言无关，stdin→stdout）
```

**评估**：依赖方向基本正确。`common/lib.rs` 作为领域核心没有外部依赖（仅 serde），`gateway` 和 `skill-server` 都向内依赖 `common`。但有几个违反点（见第 6 节）。

### 2.2 四层映射

| Clean Architecture 层 | SkillScale 对应 | 职责 |
|---|---|---|
| **Entities (领域)** | `common/lib.rs` | `SendTaskParams`, `Message`, `Part`, `Role`, `TaskResult`, `TaskState` |
| **Use Cases** | `opencode-exec`, `skill-server/main.rs` | 技能路由、匹配、执行、结果回传 |
| **Interface Adapters** | `gateway/main.rs`, `mcp_server.rs`, `skill_discovery.rs` | 协议转换 (A2A/MCP → Kafka → Reply) |
| **Frameworks & Drivers** | axum, rmcp, rdkafka, Docker, Redpanda | 基础设施与外部依赖 |

---

## 3. 限界上下文 (Bounded Contexts)

从 DDD 角度，系统可划分为以下限界上下文：

### 3.1 Gateway Context（网关上下文）

**聚合根**：`AppState`（含 producer, pending_requests, reply_topic）

| 组件 | 文件 | 职责 |
|---|---|---|
| A2A Endpoint | `gateway/main.rs` → `handle_converse()` | 接收 REST 请求，注入元数据，发 Kafka，等 Reply |
| MCP Endpoint | `mcp_server.rs` → `GatewayMcpServer` | 接收 MCP tool_call，路由到 Kafka |
| Skill Discovery | `skill_discovery.rs` | 扫描 `skills/` 目录，解析 `AGENTS.md`，注册 MCP tools |
| Reply Consumer | `main.rs` → tokio::spawn 循环 | 消费 `gateway-replies-<uuid>` topic，correlate 到 pending oneshot |

**通信协议**：
- 入站：HTTP/REST (A2A), Streamable HTTP/SSE (MCP)
- 出站：Kafka produce to `TOPIC_*`
- 回收：Kafka consume from `gateway-replies-<uuid>`

### 3.2 Skill Execution Context（技能执行上下文）

**聚合根**：Kafka Consumer 循环

| 组件 | 文件 | 职责 |
|---|---|---|
| Message Consumer | `skill-server/main.rs` → main loop | 消费 `TOPIC_*`，解析 `SendTaskParams` |
| Skill Dispatcher | `opencode-exec` (bash) | 根据 AGENTS.md 路由到具体技能脚本 |
| Skill Executor | `.claude/skills/*/scripts/*` | stdin→stdout 纯函数执行（语言无关） |
| Reply Producer | `skill-server/main.rs` | 将结果发送到 `reply_to` topic |

### 3.3 Skill Definition Context（技能定义上下文）

**值对象**：`AgentDef`, `SkillDef`, `SkillMetadata`

| 组件 | 文件 | 职责 |
|---|---|---|
| AGENTS.md | `skills/*/AGENTS.md` | 每个 topic 的技能目录（XML 标签格式） |
| SKILL.md | `.claude/skills/*/SKILL.md` | 单个技能的完整指令（给 OpenCode 读取） |
| Skill Script | `.claude/skills/*/scripts/*` | 技能的可执行入口（语言无关） |

### 3.4 SDK Context（客户端 SDK 上下文）

Python SDK 已移除。客户端直接通过 A2A REST (:8085) 或 MCP Streamable HTTP (:8086) 与 Gateway 通信，无需额外 SDK。参考 `examples/demo_a2a_client.py` 和 `examples/demo_mcp_client.py`。

---

## 4. 数据流分析

### 4.1 MCP 调用流（细粒度 skill 级别）

```
MCP Client
  │ POST http://gateway:8086/mcp (SSE)
  ▼
GatewayMcpServer::call_tool("code-analysis__dead-code-detector", {input: "..."})
  │ 解析 tool_name → category="code-analysis", skill="dead-code-detector"
  ▼
invoke_kafka(category, skill_name, input)
  │ 1. 构造 SendTaskParams { id, message, metadata{reply_to, request_id, skill} }
  │ 2. Produce → TOPIC_CODE_ANALYSIS
  │ 3. oneshot::channel() 注册到 pending_requests
  │ 4. tokio::time::timeout(600s, rx.await)
  ▼
Redpanda TOPIC_CODE_ANALYSIS
  ▼
skill-server-code-analysis (Kafka Consumer)
  │ 1. 解析 payload → SendTaskParams
  │ 2. 提取 skill_name="dead-code-detector", intent=用户输入
  │ 3. execute_skill(opencode-exec, "dead-code-detector", intent)
  │    └─ bash: opencode run → 读 AGENTS.md → 执行技能脚本 (stdin→stdout)
  │ 4. Produce reply → gateway-replies-<uuid>
  ▼
Gateway Reply Consumer
  │ 解析 metadata.request_id → 找到 oneshot::Sender → tx.send(result)
  ▼
invoke_kafka() → rx 收到结果 → 返回给 MCP Client
```

### 4.2 A2A 调用流（粗粒度 agent 级别）

```
A2A Client
  │ POST http://gateway:8085/v1/agents/code-analysis/converse
  │ Body: { id, message: { role: "user", parts: [{type: "text", text: "..."}] } }
  ▼
handle_converse(agent_id="code-analysis", params)
  │ 1. topic = "TOPIC_CODE_ANALYSIS"
  │ 2. 注入 metadata: { reply_to, request_id, skill=agent_id }
  │ 3. Produce → TOPIC_CODE_ANALYSIS
  │ 4. oneshot::channel() → pending_requests
  │ 5. timeout(600s, rx.await)
  ▼
(后续流程同 MCP)
```

### 4.3 请求 ID 关联机制

```
Gateway                         Redpanda                     Skill Server
  │                                │                              │
  ├─ gen uuid ──────────────┐      │                              │
  ├─ pending[uuid] = tx     │      │                              │
  ├─ produce(payload+uuid) ─┼──▶── TOPIC_* ──▶── consumer ──────▶│
  │                         │      │                              │
  │  (waiting rx...)        │      │        reply_to=gateway-replies-xxx
  │                         │      │        request_id=uuid       │
  │                         │      │                              │
  │                         │      │     ◀── produce(result+uuid) │
  │  ◀── consume(reply) ───┼──◀── gateway-replies-xxx            │
  ├─ pending.remove(uuid)   │      │                              │
  ├─ tx.send(result)        │      │                              │
  └─────────────────────────┘      │                              │
```

---

## 5. 核心设计决策

### 5.1 双协议并行

Gateway 同时暴露 **A2A (REST)** 和 **MCP (SSE)** 两个协议端口：

- **A2A (:8085)**：粗粒度模式。URL 路径 = agent 名称，由 skill-server 内部路由到具体技能。
- **MCP (:8086)**：细粒度模式。tool 名称编码了 `category__skill`，客户端可直接指定技能。

两者共享同一个 `AppState`（Kafka producer、pending_requests map、reply topic）。

**权衡**：MCP 在 `list_tools()` 暴露了完整的技能目录，适合 IDE/Agent 集成；A2A 更简单，适合粗粒度委托。

### 5.2 Kafka 作为唯一传输层

系统移除了早期的 ZeroMQ 直连方案，统一使用 Kafka/Redpanda 作为 Gateway→Skill Server 的唯一通信通道。

**优点**：
- 天然负载均衡（consumer group）
- 消息持久化，支持重放
- 水平扩展 skill-server 实例无需改配置

**代价**：
- 每次请求需要两次 Kafka 消息（request + reply）
- 延迟较直连高约 10-50ms
- 需要管理 reply topic 生命周期

### 5.3 OpenCode 作为技能编排器

Skill Server 不直接调用 Python 脚本，而是通过 `opencode-exec` → OpenCode CLI 间接调用：

```
skill-server (Rust) 
  → opencode-exec (bash)
    → opencode run (AI Agent)
      → 读 AGENTS.md，选择技能
      → 执行技能脚本 (stdin→stdout，语言无关)
```

**优点**：
- OpenCode 自带 AGENTS.md 解析和智能路由
- 支持 direct/hint/legacy 三种调度模式
- 技能脚本保持 stdin→stdout 纯函数接口

**代价**：
- 多层进程嵌套（Rust → bash → Go(OpenCode) → Python），启动开销约 2-5s
- 依赖外部二进制（opencode），增加部署复杂度

### 5.4 每 Topic 独立容器

每个技能类别（code-analysis, data-processing）部署为独立的 Docker 容器：

```yaml
skill-server-code-analysis:     # 消费 TOPIC_CODE_ANALYSIS
skill-server-data-processing:   # 消费 TOPIC_DATA_PROCESSING
```

**优点**：故障隔离、独立扩缩容、资源配额独立
**代价**：容器数量随技能类别线性增长

### 5.5 临时工作空间 (Ephemeral Workspace)

Skill Server 容器配置为只读文件系统 + tmpfs：

```yaml
read_only: true
tmpfs: [/tmp, /run, /var/tmp, /root/.cache, /root/.local]
security_opt: [no-new-privileges:true]
```

这确保了每次技能执行都在干净的环境中运行，避免跨请求状态污染。

---

## 6. 架构问题与改进建议

### 6.1 🔴 `common/lib.rs` 职责不清

**问题**：`common/lib.rs` 同时定义了：
- 领域模型（`SendTaskParams`, `Message`, `Part`, `Role`）
- 传输协议结构（`JsonRpcRequest`, `JsonRpcResponse`）
- 内部协议结构（`SkillRequest`, `SkillContext`）

按 DDD 原则，这三组概念属于不同的限界上下文。

**建议**：拆分为：
```
common/src/
  domain.rs      → SendTaskParams, Message, Part, Role, TaskResult
  protocol.rs    → JsonRpcRequest, JsonRpcResponse (A2A 协议)
  internal.rs    → SkillRequest, SkillContext (内部传输)
```

### 6.2 🔴 Gateway 中的 God Object

**问题**：`AppState` 承载了所有状态——Kafka producer、reply topic、pending requests map、timeout 配置。`handle_converse()` 和 `GatewayMcpServer::invoke_kafka()` 包含几乎相同的 Kafka 发送+等待逻辑（约 40 行重复代码）。

**建议**：
- 提取 `KafkaRouter` 结构体，封装 produce-and-wait-for-reply 模式
- `handle_converse()` 和 `invoke_kafka()` 都调用 `KafkaRouter::send_and_await()`

```rust
// 提取后
struct KafkaRouter {
    producer: FutureProducer,
    reply_topic: String,
    pending: Arc<Mutex<HashMap<String, oneshot::Sender<Value>>>>,
    timeout: Duration,
}

impl KafkaRouter {
    async fn send_and_await(&self, topic: &str, params: SendTaskParams) -> Result<Value, Error> {
        // 统一的 produce + wait + cleanup 逻辑
    }
}
```

### 6.3 ✅ Skill Discovery 已统一

Python SDK 已移除，技能发现现在统一由 Rust 版（`gateway/skill_discovery.rs`）处理，扫描 `skills/` 目录的 `AGENTS.md`，解析 XML 标签。

### 6.4 ✅ ZeroMQ 遗留代码已清理

`skillscale/client.py` 及整个 Python SDK 已删除。系统完全使用 Kafka 作为唯一传输层。

### 6.5 🟡 进程嵌套过深

**问题**：Skill 执行的调用链：
```
Rust binary → bash script → Go binary (OpenCode) → skill script
```
四层进程嵌套带来：
- 每次调用 2-5 秒启动开销
- 错误传播链长，调试困难
- 环境变量和工作目录管理复杂

**建议**：长期方案——Rust skill-server 直接 spawn 技能脚本进程，跳过 OpenCode CLI 中间层。保留 OpenCode 仅用于需要 AI 路由的 direct dispatch 模式。

### 6.6 🟢 领域命名改进

按 DDD 命名原则，以下名称可以更具领域语义：

| 当前命名 | 建议 | 原因 |
|---|---|---|
| `AppState` | `GatewayContext` | 反映其作为网关上下文聚合根的角色 |
| `handle_converse` | `dispatch_a2a_task` | 更准确描述动作 |
| `invoke_kafka` | `route_to_skill_server` | 领域语义而非实现细节 |
| `pending_requests` | `inflight_tasks` | 领域术语 |
| `opencode-exec` | `skill-executor` | 去除实现绑定 |

### 6.7 🟢 错误处理模式

**问题**：`handle_converse()` 返回 `Json<Value>` 而非 Result 类型。错误通过手动构造 JSON-RPC error 对象传递，无法利用 axum 的错误提取器。

**建议**：定义 `GatewayError` 枚举 + `IntoResponse` 实现：

```rust
enum GatewayError {
    KafkaProduceFailure(String),
    SkillTimeout { task_id: String, timeout: Duration },
    ChannelClosed,
}

impl IntoResponse for GatewayError {
    fn into_response(self) -> Response { /* JSON-RPC error body */ }
}
```

---

## 7. 可扩展性分析

### 7.1 水平扩展

| 组件 | 扩展方式 | 瓶颈 |
|---|---|---|
| Gateway | 多实例 + 负载均衡器 | 每个实例独立的 reply topic |
| Skill Server | 增加 consumer 实例（同 group.id 或独立容器） | OpenCode CLI 启动开销 |
| Redpanda | 增加 partition 数量 | 单 partition 内有序，跨 partition 无序 |

### 7.2 新增技能类别

1. 在 `skills/` 下创建新目录 + `AGENTS.md`
2. 在 `.claude/skills/` 下创建对应的 SKILL.md + 可执行脚本（任意语言）
3. 运行 `build.sh` → 自动生成 docker-compose.yml 中的新 service
4. Gateway 自动发现新 MCP tools（扫描 `skills/` 目录）

**评估**：扩展技能的工作流相对低摩擦，但需要同时维护两处技能定义（`skills/AGENTS.md` + `.claude/skills/*/SKILL.md`），有一定同步风险。

---

## 8. 安全边界

```
┌── Docker Network (内部) ─────────────────────────┐
│                                                   │
│  Gateway ◄──── TLS (未实现) ────► MCP/A2A Client  │
│     │                                             │
│     ▼                                             │
│  Redpanda (PLAINTEXT://29092)                     │
│     │                                             │
│     ▼                                             │
│  Skill Server (read_only, no-new-privileges)      │
│     │                                             │
│     ▼                                             │
│  Python Script (tmpfs, 隔离工作空间)               │
│                                                   │
└───────────────────────────────────────────────────┘
```

**当前状态**：
- ✅ 容器只读文件系统 + tmpfs
- ✅ `no-new-privileges` 安全策略
- ⚠️ Kafka 通信未加密（PLAINTEXT）
- ⚠️ Gateway 端口无认证
- ⚠️ API 密钥通过环境变量明文传递（docker-compose.yml 中可见）

---

## 9. 技术栈总结

| 层 | 技术 | 版本 |
|---|---|---|
| Gateway | Rust + axum | edition 2021 |
| MCP Protocol | rmcp (Rust MCP SDK) | git HEAD |
| Message Broker | Redpanda (Kafka-compatible) | v23.3.6 |
| Kafka Client (Rust) | rdkafka | 0.36 |
| Skill Server | Rust + tokio | 1.0 |
| AI Orchestrator | OpenCode CLI | 1.2.6 |
| Skill Runtime | Any (stdin→stdout) | language-agnostic |
| Container | Docker Compose | — |

---

## 10. 结论

SkillScale 的架构在宏观层面遵循了 Clean Architecture 的依赖方向原则——外层框架依赖内层领域模型，内层无外部依赖。Kafka 作为传输层的选择提供了良好的解耦与扩展性。

**核心优势**：
- 双协议（MCP + A2A）适配不同客户端场景
- Kafka 消费者组实现天然负载均衡
- 只读容器 + tmpfs 的安全隔离
- 技能扩展流程自动化（build.sh 自动生成 compose）

**主要改进方向**：
1. 消除 Gateway 中的 Kafka 发送+等待重复代码（提取 `KafkaRouter`）
2. 拆分 `common/lib.rs` 为领域 / 协议 / 内部三个模块
3. 清理 ZeroMQ 遗留代码
4. 减少 Skill 执行的进程嵌套层数
5. 增加 Kafka TLS 和 Gateway 认证

这些改进不需要大规模重构，可以逐步演进，保持系统在每次变更后始终可运行。
