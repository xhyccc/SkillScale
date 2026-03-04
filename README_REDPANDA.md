# SkillScale — Enterprise Distribution (Redpanda/Kafka)

This distribution of SkillScale uses **Redpanda** (a Kafka-compatible streaming platform) instead of ZeroMQ for robust, persistent, and scalable message passing.

It is designed for:
- Distributed deployments (Kubernetes, Multi-Cloud).
- Durable message storage (Replay capability).
- High-throughput analytics and monitoring via Redpanda Console.

## Architecture

1. **Agent (Producer)**: Sends intent requests to topic (e.g., `SKILL_TOPIC_CODE_ANALYSIS`).
2. **Redpanda (Broker)**: Persists messages and handles consumer groups.
3. **Skill Server (Consumer)**: Python-based worker group that pulls tasks, executes skills, and replies.

## Usage

### 1. Build & Setup
Building the Python-based Kafka containers:

```bash
./build_with_redpanda.sh
```

### 2. Run Stack
Launch Redpanda, Console, Agent, and Skill Servers:

```bash
./run_with_redpanda.sh
```

### 3. Verification
- **Redpanda Console**: Open [http://localhost:8080](http://localhost:8080) to inspect topics and messages.
- **Logs**:
  ```bash
  docker compose -f docker-compose-redpanda.yml logs -f
  ```

## Components

| Service | File | Description |
| :--- | :--- | :--- |
| **Broker** | `docker-compose-redpanda.yml` | Redpanda v23.3 single-node cluster. |
| **Server** | `skillscale/server_kafka.py` | Python implementation of Skill Server using `aiokafka`. |
| **Logic** | `skillscale/kafka.py` | Protocol implementation (RPC over Kafka). |
| **Agent** | `agent/main_kafka.py` | CLI using Kafka Producer/Consumer. |

## Differences from Standard (ZeroMQ)
- **Persistence**: Messages are stored on disk.
- **Language**: Skill Servers are Python-based (for easier Kafka integration) rather than C++.
- **Scaling**: Add more Skill Server replicas to the consumer group to scale processing.
