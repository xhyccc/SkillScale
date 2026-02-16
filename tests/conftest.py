"""
SkillScale Integration Tests — Fixtures

Provides a real ZeroMQ XPUB/XSUB proxy running in a background thread,
so all tests exercise actual network I/O without mocks.
"""

import asyncio
import json
import os
import sys
import threading
import time

import pytest
import zmq
import zmq.asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))


# ──────────────────────────────────────────────────────────
#  Real XPUB/XSUB Proxy (Python implementation for tests)
# ──────────────────────────────────────────────────────────
class TestProxy:
    """
    Runs a real ZeroMQ XPUB/XSUB proxy in a background thread.
    Uses dynamic port binding to avoid conflicts.
    """

    def __init__(self):
        self.ctx = zmq.Context()
        self.xsub_port = None
        self.xpub_port = None
        self._thread = None
        self._running = False

    def start(self):
        """Bind to random ports and start the proxy thread."""
        # XSUB socket — publishers connect here
        self._xsub = self.ctx.socket(zmq.XSUB)
        self._xsub.setsockopt(zmq.RCVHWM, 50000)
        self._xsub.setsockopt(zmq.SNDHWM, 50000)
        self.xsub_port = self._xsub.bind_to_random_port("tcp://127.0.0.1")

        # XPUB socket — subscribers connect here
        self._xpub = self.ctx.socket(zmq.XPUB)
        self._xpub.setsockopt(zmq.RCVHWM, 50000)
        self._xpub.setsockopt(zmq.SNDHWM, 50000)
        self._xpub.setsockopt(zmq.XPUB_VERBOSE, 1)
        self.xpub_port = self._xpub.bind_to_random_port("tcp://127.0.0.1")

        self._running = True
        self._thread = threading.Thread(target=self._proxy_loop, daemon=True)
        self._thread.start()

        # Allow binding to settle
        time.sleep(0.1)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._xsub.close()
        self._xpub.close()
        self.ctx.term()

    def _proxy_loop(self):
        poller = zmq.Poller()
        poller.register(self._xsub, zmq.POLLIN)
        poller.register(self._xpub, zmq.POLLIN)

        while self._running:
            try:
                events = dict(poller.poll(100))
            except zmq.ZMQError:
                break

            if self._xsub in events:
                while True:
                    try:
                        msg = self._xsub.recv(zmq.NOBLOCK)
                        more = self._xsub.getsockopt(zmq.RCVMORE)
                        self._xpub.send(msg,
                            zmq.SNDMORE if more else 0)
                    except zmq.Again:
                        break

            if self._xpub in events:
                while True:
                    try:
                        msg = self._xpub.recv(zmq.NOBLOCK)
                        more = self._xpub.getsockopt(zmq.RCVMORE)
                        self._xsub.send(msg,
                            zmq.SNDMORE if more else 0)
                    except zmq.Again:
                        break

    @property
    def xsub_addr(self):
        return f"tcp://127.0.0.1:{self.xsub_port}"

    @property
    def xpub_addr(self):
        return f"tcp://127.0.0.1:{self.xpub_port}"


# ──────────────────────────────────────────────────────────
#  Mock Skill Server (Python, for tests — mimics C++ server)
# ──────────────────────────────────────────────────────────
class MockSkillServer:
    """
    Lightweight Python skill server that mimics the C++ server behavior.
    Subscribes to a topic, executes a handler function, publishes responses.
    """

    def __init__(self, proxy_xsub: str, proxy_xpub: str, topic: str,
                 handler=None):
        self.proxy_xsub = proxy_xsub
        self.proxy_xpub = proxy_xpub
        self.topic = topic
        self.handler = handler or self._default_handler
        self._thread = None
        self._running = False
        self.requests_received = []

    def _default_handler(self, intent: str) -> str:
        return f"Processed: {intent}"

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        time.sleep(0.3)  # subscription propagation

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        ctx = zmq.Context()

        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.RCVHWM, 10000)
        sub.connect(self.proxy_xpub)
        sub.setsockopt_string(zmq.SUBSCRIBE, self.topic)

        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.SNDHWM, 10000)
        pub.setsockopt(zmq.LINGER, 1000)
        pub.connect(self.proxy_xsub)

        time.sleep(0.2)  # subscription sync

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        while self._running:
            events = dict(poller.poll(100))
            if sub not in events:
                continue

            try:
                frames = sub.recv_multipart(zmq.NOBLOCK)
                if len(frames) < 2:
                    continue

                topic = frames[0].decode("utf-8")
                payload = json.loads(frames[1].decode("utf-8"))

                self.requests_received.append(payload)

                request_id = payload.get("request_id", "unknown")
                reply_to = payload.get("reply_to", "")
                intent = payload.get("intent", "")

                # Execute handler
                try:
                    content = self.handler(intent)
                    response = {
                        "request_id": request_id,
                        "status": "success",
                        "content": content,
                        "error": "",
                        "timestamp": time.time(),
                    }
                except Exception as e:
                    response = {
                        "request_id": request_id,
                        "status": "error",
                        "content": "",
                        "error": str(e),
                        "timestamp": time.time(),
                    }

                pub.send_multipart([
                    reply_to.encode("utf-8"),
                    json.dumps(response).encode("utf-8"),
                ])

            except zmq.Again:
                pass
            except Exception as e:
                print(f"[mock-server] Error: {e}")

        sub.close()
        pub.close()
        ctx.term()


# ──────────────────────────────────────────────────────────
#  Pytest Fixtures
# ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def proxy():
    """Session-scoped real ZeroMQ proxy."""
    p = TestProxy()
    p.start()
    yield p
    p.stop()


@pytest.fixture
def mock_skill_server(proxy):
    """Factory fixture for creating mock skill servers."""
    servers = []

    def _create(topic: str, handler=None):
        server = MockSkillServer(
            proxy_xsub=proxy.xsub_addr,
            proxy_xpub=proxy.xpub_addr,
            topic=topic,
            handler=handler,
        )
        server.start()
        servers.append(server)
        return server

    yield _create

    for s in servers:
        s.stop()


@pytest.fixture
async def agent(proxy):
    """Create and start a SkillScaleAgent connected to the test proxy."""
    from main import SkillScaleAgent, AgentConfig

    config = AgentConfig(
        proxy_xsub=proxy.xsub_addr,
        proxy_xpub=proxy.xpub_addr,
        default_timeout=10.0,
    )
    a = SkillScaleAgent(config)
    await a.start()
    yield a
    await a.stop()
