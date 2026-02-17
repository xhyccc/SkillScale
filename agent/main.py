"""
SkillScale — Python Front-End Agent Orchestrator

Lightweight async orchestrator with two tools:
  - publish(topic, intent)  → broadcasts to ZeroMQ skill servers
  - respond(content)        → returns markdown to the user

Uses the skillscale SDK middleware client under the hood.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Ensure the SDK is importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skillscale.client import SkillScaleClient, ClientConfig

logging.basicConfig(
    level=logging.INFO,
    format="[agent] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("skillscale-agent")


# ──────────────────────────────────────────────────────────
#  Configuration (wraps SDK ClientConfig)
# ──────────────────────────────────────────────────────────
@dataclass
class AgentConfig:
    proxy_xsub: str = "tcp://127.0.0.1:5444"  # our PUB connects here
    proxy_xpub: str = "tcp://127.0.0.1:5555"  # our SUB connects here
    agent_id: str = field(default_factory=lambda: f"AGENT_REPLY_{uuid.uuid4().hex[:8]}")
    hwm: int = 10000
    default_timeout: float = 30.0  # seconds
    heartbeat_ivl: int = 5000  # ms

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            proxy_xsub=os.getenv("SKILLSCALE_PROXY_XSUB", cls.proxy_xsub),
            proxy_xpub=os.getenv("SKILLSCALE_PROXY_XPUB", cls.proxy_xpub),
            default_timeout=float(os.getenv("SKILLSCALE_TIMEOUT", str(cls.default_timeout))),
        )

    def to_client_config(self) -> ClientConfig:
        return ClientConfig(
            proxy_xsub=self.proxy_xsub,
            proxy_xpub=self.proxy_xpub,
            client_id=self.agent_id,
            hwm=self.hwm,
            default_timeout=self.default_timeout,
            heartbeat_ivl=self.heartbeat_ivl,
        )


# ──────────────────────────────────────────────────────────
#  Agent core — thin wrapper around SkillScaleClient
# ──────────────────────────────────────────────────────────
class SkillScaleAgent:
    """
    Front-end agent with two tools: publish() and respond().
    Delegates all ZMQ work to the SkillScaleClient middleware.
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig.from_env()
        self._client = SkillScaleClient(self.config.to_client_config())

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self):
        await self._client.connect()

    async def stop(self):
        await self._client.close()

    # ── Tool 1: publish(topic, intent) ─────────────────────

    async def publish(self, topic: str, intent: str,
                      timeout: Optional[float] = None) -> str:
        return await self._client.invoke(topic, intent, timeout)

    # ── Tool 2: respond(content) ───────────────────────────

    def respond(self, content: str) -> str:
        self._client.gc_stale()
        log.info("Responding with %d chars of markdown", len(content))
        return content

    # ── Multi-publish helpers ──────────────────────────────

    async def publish_parallel(self, requests: list[tuple[str, str]],
                                timeout: Optional[float] = None) -> list[str]:
        return await self._client.invoke_parallel(requests, timeout)

    async def publish_sequential(self, requests: list[tuple[str, str]],
                                  timeout: Optional[float] = None) -> list[str]:
        return await self._client.invoke_sequential(requests, timeout)


# ──────────────────────────────────────────────────────────
#  Interactive CLI mode (for local testing)
# ──────────────────────────────────────────────────────────
async def interactive_cli():
    """Simple REPL for testing the agent locally."""
    config = AgentConfig.from_env()
    agent = SkillScaleAgent(config)
    await agent.start()

    print("\n╔══════════════════════════════════════════╗")
    print("║   SkillScale Agent — Interactive Mode    ║")
    print("╠══════════════════════════════════════════╣")
    print("║  Commands:                               ║")
    print("║    pub <topic> <intent>                  ║")
    print("║    topics                                ║")
    print("║    quit                                  ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("agent> ")
                )
            except EOFError:
                break

            line = line.strip()
            if not line:
                continue

            if line.lower() in ("quit", "exit", "q"):
                break

            if line.lower() == "topics":
                print("Known topics: (discover via skill server metadata)")
                continue

            if line.lower().startswith("pub "):
                parts = line.split(maxsplit=2)
                if len(parts) < 3:
                    print("Usage: pub <TOPIC_NAME> <intent text>")
                    continue

                topic = parts[1]
                intent = parts[2]

                try:
                    result = await agent.publish(topic, intent)
                    response = agent.respond(result)
                    print(f"\n--- Response ---\n{response}\n")
                except asyncio.TimeoutError:
                    print("\n⚠  Timeout: No skill server responded.\n")
                except RuntimeError as e:
                    print(f"\n⚠  Error: {e}\n")
                continue

            print(f"Unknown command: {line}")

    finally:
        await agent.stop()


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Handle SIGINT/SIGTERM gracefully
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: loop.stop())

    try:
        loop.run_until_complete(interactive_cli())
    except (KeyboardInterrupt, RuntimeError):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
