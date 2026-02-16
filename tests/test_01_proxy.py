"""
Test 1: Raw ZeroMQ Proxy Message Routing

Validates that the XPUB/XSUB proxy correctly:
  - Forwards messages from publisher to subscriber
  - Routes subscription frames upstream
  - Performs prefix-based topic filtering
  - Handles multi-frame messages
"""

import json
import time

import pytest
import zmq


class TestProxyRouting:
    """Tests the real XPUB/XSUB proxy without any agent logic."""

    def test_basic_pubsub_through_proxy(self, proxy):
        """A message published to XSUB arrives at a subscriber on XPUB."""
        ctx = zmq.Context()

        # Subscriber connects to XPUB
        sub = ctx.socket(zmq.SUB)
        sub.connect(proxy.xpub_addr)
        sub.setsockopt_string(zmq.SUBSCRIBE, "TEST_TOPIC")

        # Publisher connects to XSUB
        pub = ctx.socket(zmq.PUB)
        pub.connect(proxy.xsub_addr)

        # Subscription propagation delay
        time.sleep(0.5)

        # Publish
        pub.send_multipart([b"TEST_TOPIC", b"hello world"])

        # Receive
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        events = dict(poller.poll(3000))
        assert sub in events, "Subscriber did not receive message"

        frames = sub.recv_multipart()
        assert frames[0] == b"TEST_TOPIC"
        assert frames[1] == b"hello world"

        sub.close()
        pub.close()
        ctx.term()

    def test_topic_filtering(self, proxy):
        """Messages for unsubscribed topics are not delivered."""
        ctx = zmq.Context()

        sub = ctx.socket(zmq.SUB)
        sub.connect(proxy.xpub_addr)
        sub.setsockopt_string(zmq.SUBSCRIBE, "WANTED_TOPIC")

        pub = ctx.socket(zmq.PUB)
        pub.connect(proxy.xsub_addr)

        time.sleep(0.5)

        # Send to a topic we did NOT subscribe to
        pub.send_multipart([b"UNWANTED_TOPIC", b"should not arrive"])
        # Send to the topic we DID subscribe to
        pub.send_multipart([b"WANTED_TOPIC", b"should arrive"])

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)

        received = []
        deadline = time.time() + 2.0
        while time.time() < deadline:
            events = dict(poller.poll(200))
            if sub in events:
                frames = sub.recv_multipart()
                received.append(frames[0].decode())

        assert "WANTED_TOPIC" in received
        assert "UNWANTED_TOPIC" not in received

        sub.close()
        pub.close()
        ctx.term()

    def test_json_payload_integrity(self, proxy):
        """JSON payload survives transit through the proxy intact."""
        ctx = zmq.Context()

        sub = ctx.socket(zmq.SUB)
        sub.connect(proxy.xpub_addr)
        sub.setsockopt_string(zmq.SUBSCRIBE, "JSON_TEST")

        pub = ctx.socket(zmq.PUB)
        pub.connect(proxy.xsub_addr)

        time.sleep(0.5)

        original = {
            "request_id": "abc123",
            "reply_to": "AGENT_REPLY_xyz",
            "intent": "Summarize this document about quantum computing.",
            "timestamp": time.time(),
            "nested": {"key": [1, 2, 3]},
        }

        pub.send_multipart([
            b"JSON_TEST",
            json.dumps(original).encode("utf-8"),
        ])

        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        events = dict(poller.poll(3000))
        assert sub in events

        frames = sub.recv_multipart()
        received = json.loads(frames[1].decode("utf-8"))

        assert received["request_id"] == original["request_id"]
        assert received["intent"] == original["intent"]
        assert received["nested"]["key"] == [1, 2, 3]

        sub.close()
        pub.close()
        ctx.term()

    def test_multiple_subscribers_same_topic(self, proxy):
        """Multiple subscribers on the same topic all receive the message."""
        ctx = zmq.Context()

        subs = []
        for _ in range(3):
            s = ctx.socket(zmq.SUB)
            s.connect(proxy.xpub_addr)
            s.setsockopt_string(zmq.SUBSCRIBE, "MULTI_SUB")
            subs.append(s)

        pub = ctx.socket(zmq.PUB)
        pub.connect(proxy.xsub_addr)

        time.sleep(0.5)

        pub.send_multipart([b"MULTI_SUB", b"broadcast"])

        for i, sub in enumerate(subs):
            poller = zmq.Poller()
            poller.register(sub, zmq.POLLIN)
            events = dict(poller.poll(3000))
            assert sub in events, f"Subscriber {i} did not receive message"
            frames = sub.recv_multipart()
            assert frames[1] == b"broadcast"

        for s in subs:
            s.close()
        pub.close()
        ctx.term()

    def test_bidirectional_flow(self, proxy):
        """Two peers can both publish and subscribe through the proxy."""
        ctx = zmq.Context()

        # Peer A: PUB + SUB
        pub_a = ctx.socket(zmq.PUB)
        pub_a.connect(proxy.xsub_addr)
        sub_a = ctx.socket(zmq.SUB)
        sub_a.connect(proxy.xpub_addr)
        sub_a.setsockopt_string(zmq.SUBSCRIBE, "FROM_B")

        # Peer B: PUB + SUB
        pub_b = ctx.socket(zmq.PUB)
        pub_b.connect(proxy.xsub_addr)
        sub_b = ctx.socket(zmq.SUB)
        sub_b.connect(proxy.xpub_addr)
        sub_b.setsockopt_string(zmq.SUBSCRIBE, "FROM_A")

        time.sleep(0.5)

        # A → B
        pub_a.send_multipart([b"FROM_A", b"hello B"])
        # B → A
        pub_b.send_multipart([b"FROM_B", b"hello A"])

        poller_a = zmq.Poller()
        poller_a.register(sub_a, zmq.POLLIN)
        poller_b = zmq.Poller()
        poller_b.register(sub_b, zmq.POLLIN)

        events_a = dict(poller_a.poll(3000))
        assert sub_a in events_a
        frames_a = sub_a.recv_multipart()
        assert frames_a[1] == b"hello A"

        events_b = dict(poller_b.poll(3000))
        assert sub_b in events_b
        frames_b = sub_b.recv_multipart()
        assert frames_b[1] == b"hello B"

        for s in (pub_a, sub_a, pub_b, sub_b):
            s.close()
        ctx.term()
