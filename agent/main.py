"""
SkillScale — Python Front-End Agent Orchestrator

Lightweight async orchestrator with two tools:
  - publish(topic, intent)  → broadcasts to ZeroMQ skill servers
  - respond(content)        → returns markdown to the user

Uses pyzmq async sockets with asyncio for fully non-blocking I/O.
"""

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import zmq
import zmq.asyncio

logging.basicConfig(
    level=logging.INFO,
    format="[agent] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("skillscale-agent")


# ──────────────────────────────────────────────────────────
#  Configuration
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


# ──────────────────────────────────────────────────────────
#  Pending request tracker
# ──────────────────────────────────────────────────────────
@dataclass
class PendingRequest:
    request_id: str
    topic: str
    intent: str
    future: asyncio.Future
    created_at: float = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────
#  Agent core
# ──────────────────────────────────────────────────────────
class SkillScaleAgent:
    """
    Front-end agent with async ZeroMQ PUB/SUB and two tools:
    publish() and respond().
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig.from_env()
        self._ctx: Optional[zmq.asyncio.Context] = None
        self._pub: Optional[zmq.asyncio.Socket] = None
        self._sub: Optional[zmq.asyncio.Socket] = None
        self._pending: Dict[str, PendingRequest] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False
        self._skill_metadata: Dict[str, Any] = {}  # topic → metadata

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self):
        """Initialize ZeroMQ sockets and start the background listener."""
        log.info("Starting agent (id=%s)", self.config.agent_id)

        self._ctx = zmq.asyncio.Context()

        # PUB socket → connects to proxy XSUB (port 5444)
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, self.config.hwm)
        self._pub.setsockopt(zmq.LINGER, 1000)
        self._pub.connect(self.config.proxy_xsub)

        # SUB socket → connects to proxy XPUB (port 5555)
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.RCVHWM, self.config.hwm)
        self._sub.setsockopt(zmq.TCP_KEEPALIVE, 1)
        self._sub.setsockopt(zmq.HEARTBEAT_IVL, self.config.heartbeat_ivl)
        self._sub.setsockopt(zmq.HEARTBEAT_TTL, self.config.heartbeat_ivl * 3)
        self._sub.setsockopt(zmq.HEARTBEAT_TIMEOUT, self.config.heartbeat_ivl * 3)
        self._sub.setsockopt(zmq.RECONNECT_IVL, 100)
        self._sub.setsockopt(zmq.RECONNECT_IVL_MAX, 5000)
        self._sub.connect(self.config.proxy_xpub)

        # Subscribe to our unique reply topic
        self._sub.setsockopt_string(zmq.SUBSCRIBE, self.config.agent_id)
        log.info("Subscribed to reply topic: %s", self.config.agent_id)

        # Late-joiner mitigation: wait for subscription propagation
        await asyncio.sleep(0.5)

        self._running = True
        self._listener_task = asyncio.create_task(self._listener_loop())
        log.info("Agent ready.")

    async def stop(self):
        """Clean shutdown."""
        log.info("Shutting down agent...")
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        # Cancel all pending futures
        for req in self._pending.values():
            if not req.future.done():
                req.future.cancel()
        self._pending.clear()

        if self._pub:
            self._pub.close()
        if self._sub:
            self._sub.close()
        if self._ctx:
            self._ctx.term()

        log.info("Agent shutdown complete.")

    # ── Tool 1: publish(topic, intent) ─────────────────────

    async def publish(self, topic: str, intent: str,
                      timeout: Optional[float] = None) -> str:
        """
        Broadcast an intent to a specific skill topic.

        Returns the skill server's response content (markdown string).
        Raises asyncio.TimeoutError if no response within timeout.
        """
        timeout = timeout or self.config.default_timeout
        request_id = uuid.uuid4().hex

        payload = json.dumps({
            "request_id": request_id,
            "reply_to": self.config.agent_id,
            "intent": intent,
            "timestamp": time.time(),
        })

        # Register pending future
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = PendingRequest(
            request_id=request_id,
            topic=topic,
            intent=intent,
            future=future,
        )

        # Send multipart: [topic, payload]
        await self._pub.send_multipart([
            topic.encode("utf-8"),
            payload.encode("utf-8"),
        ])
        log.info("Published intent to %s (req=%s)", topic, request_id[:8])

        # Wait for response with timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            log.warning("Timeout waiting for response (req=%s, topic=%s)",
                        request_id[:8], topic)
            self._pending.pop(request_id, None)
            raise

    # ── Tool 2: respond(content) ───────────────────────────

    def respond(self, content: str) -> str:
        """
        Terminal action: return formatted markdown to the user.
        Flushes session state and garbage-collects stale futures.
        """
        # GC stale pending requests (older than 2× default timeout)
        cutoff = time.time() - (self.config.default_timeout * 2)
        stale_ids = [
            rid for rid, req in self._pending.items()
            if req.created_at < cutoff
        ]
        for rid in stale_ids:
            req = self._pending.pop(rid)
            if not req.future.done():
                req.future.cancel()
            log.debug("GC'd stale request %s", rid[:8])

        log.info("Responding with %d chars of markdown", len(content))
        return content

    # ── Multi-publish helpers ──────────────────────────────

    async def publish_parallel(self, requests: list[tuple[str, str]],
                                timeout: Optional[float] = None) -> list[str]:
        """
        Publish multiple intents in parallel and gather all responses.

        Args:
            requests: list of (topic, intent) tuples
            timeout:  per-request timeout

        Returns:
            List of response strings (in same order as requests)
        """
        tasks = [self.publish(topic, intent, timeout) for topic, intent in requests]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def publish_sequential(self, requests: list[tuple[str, str]],
                                  timeout: Optional[float] = None) -> list[str]:
        """
        Publish intents sequentially (each waits for the previous to complete).
        """
        results = []
        for topic, intent in requests:
            result = await self.publish(topic, intent, timeout)
            results.append(result)
        return results

    # ── Background listener ────────────────────────────────

    async def _listener_loop(self):
        """
        Continuously polls the SUB socket for incoming responses
        and resolves the corresponding pending futures.
        """
        log.info("Listener loop started")
        poller = zmq.asyncio.Poller()
        poller.register(self._sub, zmq.POLLIN)

        while self._running:
            try:
                events = await poller.poll(timeout=250)
            except asyncio.CancelledError:
                break

            for sock, _ in events:
                try:
                    frames = await sock.recv_multipart(zmq.NOBLOCK)
                    if len(frames) < 2:
                        log.warning("Received malformed message (frames=%d)", len(frames))
                        continue

                    topic = frames[0].decode("utf-8")
                    payload_str = frames[1].decode("utf-8")

                    try:
                        payload = json.loads(payload_str)
                    except json.JSONDecodeError as e:
                        log.error("Invalid JSON in response: %s", e)
                        continue

                    request_id = payload.get("request_id")
                    status = payload.get("status", "unknown")
                    content = payload.get("content", "")
                    error = payload.get("error", "")

                    log.info("Received response for req=%s status=%s",
                             (request_id or "?")[:8], status)

                    if request_id and request_id in self._pending:
                        pending = self._pending.pop(request_id)
                        if not pending.future.done():
                            if status == "success":
                                pending.future.set_result(content)
                            else:
                                pending.future.set_exception(
                                    RuntimeError(f"Skill error: {error}")
                                )
                    else:
                        log.warning("Received response for unknown request: %s",
                                    (request_id or "?")[:8])

                except zmq.Again:
                    pass
                except Exception as e:
                    log.error("Listener error: %s", e, exc_info=True)

        log.info("Listener loop stopped")


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
