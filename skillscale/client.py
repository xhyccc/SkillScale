"""
SkillScale Core Client — Framework-agnostic async ZeroMQ pub/sub client.

This is the middleware heart. It manages ZMQ connections to the XPUB/XSUB proxy,
publishes intents, correlates responses by request_id, and enforces timeouts.

Any agent framework (LangChain, LangGraph, CrewAI, etc.) wraps this client
through thin adapter layers.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import zmq
import zmq.asyncio

log = logging.getLogger("skillscale")


# ──────────────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────────────
@dataclass
class ClientConfig:
    """Connection and tuning parameters for the SkillScale middleware."""

    proxy_xsub: str = "tcp://127.0.0.1:5444"   # PUB connects here
    proxy_xpub: str = "tcp://127.0.0.1:5555"   # SUB connects here
    client_id: str = field(
        default_factory=lambda: f"AGENT_REPLY_{uuid.uuid4().hex[:8]}"
    )
    hwm: int = 10_000                           # ZMQ high-water mark
    default_timeout: float = 30.0               # seconds
    heartbeat_ivl: int = 5000                   # ms
    settle_time: float = 0.5                    # sec — subscription propagation
    stale_multiplier: float = 2.0               # GC stale requests after N×timeout

    @classmethod
    def from_env(cls) -> "ClientConfig":
        return cls(
            proxy_xsub=os.getenv("SKILLSCALE_PROXY_XSUB", cls.proxy_xsub),
            proxy_xpub=os.getenv("SKILLSCALE_PROXY_XPUB", cls.proxy_xpub),
            default_timeout=float(
                os.getenv("SKILLSCALE_TIMEOUT", str(cls.default_timeout))
            ),
        )


# ──────────────────────────────────────────────────────────
#  Pending request bookkeeping
# ──────────────────────────────────────────────────────────
@dataclass
class _PendingRequest:
    request_id: str
    topic: str
    intent: str
    future: asyncio.Future
    created_at: float = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────
#  Core Client
# ──────────────────────────────────────────────────────────
class SkillScaleClient:
    """
    Async ZeroMQ client that publishes intents to the SkillScale proxy
    and awaits correlated responses from C++ Skill Servers.

    This is the **middleware layer** — framework adapters (LangChain,
    LangGraph, CrewAI, etc.) wrap this client with thin tool abstractions.

    Lifecycle:
        client = SkillScaleClient(config)
        await client.connect()
        result = await client.invoke("TOPIC_DATA_PROCESSING", "some intent")
        await client.close()
    """

    def __init__(self, config: Optional[ClientConfig] = None):
        self.config = config or ClientConfig.from_env()
        self._ctx: Optional[zmq.asyncio.Context] = None
        self._pub: Optional[zmq.asyncio.Socket] = None
        self._sub: Optional[zmq.asyncio.Socket] = None
        self._pending: Dict[str, _PendingRequest] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False

    # ── Properties ─────────────────────────────────────────

    @property
    def client_id(self) -> str:
        return self.config.client_id

    @property
    def is_connected(self) -> bool:
        return self._running

    # ── Lifecycle ──────────────────────────────────────────

    async def connect(self):
        """Initialize ZMQ sockets and start the background listener."""
        if self._running:
            return

        log.info("Connecting client (id=%s)", self.config.client_id)

        self._ctx = zmq.asyncio.Context()

        # PUB → proxy XSUB
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.SNDHWM, self.config.hwm)
        self._pub.setsockopt(zmq.LINGER, 1000)
        self._pub.connect(self.config.proxy_xsub)

        # SUB → proxy XPUB
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
        self._sub.setsockopt_string(zmq.SUBSCRIBE, self.config.client_id)
        log.info("Subscribed to reply topic: %s", self.config.client_id)

        # Wait for subscription propagation through the proxy
        await asyncio.sleep(self.config.settle_time)

        self._running = True
        self._listener_task = asyncio.create_task(self._listener_loop())
        log.info("Client ready.")

    async def close(self):
        """Cleanly shut down sockets and cancel pending futures."""
        log.info("Shutting down client...")
        self._running = False

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

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

        log.info("Client shutdown complete.")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ── Core API ───────────────────────────────────────────

    async def invoke(
        self,
        topic: str,
        intent: str,
        timeout: Optional[float] = None,
    ) -> str:
        """
        Publish an intent to a skill topic and await the response.

        Args:
            topic:   ZMQ topic prefix (e.g. "TOPIC_DATA_PROCESSING")
            intent:  free-form string or JSON payload passed to the skill
            timeout: per-call timeout in seconds (overrides default)

        Returns:
            The skill server's markdown response content.

        Raises:
            asyncio.TimeoutError: no response within timeout
            RuntimeError: skill server returned an error status
            ConnectionError: client is not connected
        """
        if not self._running:
            raise ConnectionError("Client is not connected. Call connect() first.")

        timeout = timeout or self.config.default_timeout
        request_id = uuid.uuid4().hex

        payload = json.dumps({
            "request_id": request_id,
            "reply_to": self.config.client_id,
            "intent": intent,
            "timestamp": time.time(),
        })

        # Register pending future
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = _PendingRequest(
            request_id=request_id,
            topic=topic,
            intent=intent,
            future=future,
        )

        # Send multipart [topic, payload]
        await self._pub.send_multipart([
            topic.encode("utf-8"),
            payload.encode("utf-8"),
        ])
        log.info("Published intent to %s (req=%s)", topic, request_id[:8])

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            log.warning("Timeout (req=%s, topic=%s)", request_id[:8], topic)
            self._pending.pop(request_id, None)
            raise

    async def invoke_parallel(
        self,
        requests: List[Tuple[str, str]],
        timeout: Optional[float] = None,
    ) -> List[str]:
        """
        Publish multiple intents in parallel and gather all responses.

        Args:
            requests: list of (topic, intent) tuples
            timeout:  per-request timeout

        Returns:
            List of response strings (same order as input).
            Failed requests appear as Exception instances.
        """
        tasks = [self.invoke(topic, intent, timeout) for topic, intent in requests]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def invoke_sequential(
        self,
        requests: List[Tuple[str, str]],
        timeout: Optional[float] = None,
    ) -> List[str]:
        """Publish intents one after another, each awaiting before the next."""
        results = []
        for topic, intent in requests:
            result = await self.invoke(topic, intent, timeout)
            results.append(result)
        return results

    # ── Housekeeping ───────────────────────────────────────

    def gc_stale(self):
        """Cancel and remove futures that have exceeded the stale threshold."""
        cutoff = time.time() - (
            self.config.default_timeout * self.config.stale_multiplier
        )
        stale_ids = [
            rid for rid, req in self._pending.items()
            if req.created_at < cutoff
        ]
        for rid in stale_ids:
            req = self._pending.pop(rid)
            if not req.future.done():
                req.future.cancel()
            log.debug("GC'd stale request %s", rid[:8])
        return len(stale_ids)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ── Background listener ────────────────────────────────

    async def _listener_loop(self):
        """Poll the SUB socket and resolve matching futures."""
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
                        log.warning("Malformed message (frames=%d)", len(frames))
                        continue

                    payload_str = frames[1].decode("utf-8")
                    try:
                        payload = json.loads(payload_str)
                    except json.JSONDecodeError as e:
                        log.error("Invalid JSON: %s", e)
                        continue

                    request_id = payload.get("request_id")
                    status = payload.get("status", "unknown")
                    content = payload.get("content", "")
                    error = payload.get("error", "")

                    log.info(
                        "Response for req=%s status=%s",
                        (request_id or "?")[:8], status,
                    )

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
                        log.warning("Unknown request_id: %s", (request_id or "?")[:8])

                except zmq.Again:
                    pass
                except Exception as e:
                    log.error("Listener error: %s", e, exc_info=True)

        log.info("Listener loop stopped")
