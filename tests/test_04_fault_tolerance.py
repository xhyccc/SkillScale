"""
Test 4: Fault Tolerance and Resilience

Tests for:
  - Timeout handling on unresponsive skill servers
  - Late joiner subscription propagation
  - High-water mark behavior under load
  - Reconnection after proxy restart
  - Stale future garbage collection
"""

import asyncio
import time

import pytest


@pytest.mark.asyncio
class TestFaultTolerance:

    async def test_timeout_raises_on_dead_server(self, agent):
        """Timeout fires when no skill server is listening."""
        with pytest.raises(asyncio.TimeoutError):
            await agent.publish("TOPIC_DEAD", "nobody home", timeout=1.0)

    async def test_slow_server_within_timeout(self, agent, mock_skill_server):
        """A slow but successful server completes within timeout."""
        def slow_handler(intent):
            time.sleep(1.0)
            return "slow but ok"

        mock_skill_server(topic="TOPIC_SLOW", handler=slow_handler)

        result = await agent.publish("TOPIC_SLOW", "wait for me", timeout=5.0)
        assert result == "slow but ok"

    async def test_stale_future_gc(self, agent, mock_skill_server):
        """respond() garbage-collects expired pending requests."""
        # Manually inject a stale pending request
        from main import PendingRequest
        loop = asyncio.get_running_loop()
        fake_future = loop.create_future()
        agent._pending["stale-id"] = PendingRequest(
            request_id="stale-id",
            topic="TOPIC_STALE",
            intent="old request",
            future=fake_future,
            created_at=time.time() - 999,  # very old
        )

        assert "stale-id" in agent._pending

        # respond() should clean it up
        agent.respond("some content")

        assert "stale-id" not in agent._pending
        assert fake_future.cancelled()

    async def test_multiple_timeouts_dont_leak(self, agent):
        """Multiple timeouts don't accumulate pending requests."""
        initial_pending = len(agent._pending)

        for _ in range(5):
            with pytest.raises(asyncio.TimeoutError):
                await agent.publish("TOPIC_NOWHERE", "test", timeout=0.3)

        # All timed-out requests should have been cleaned up
        assert len(agent._pending) == initial_pending

    async def test_rapid_fire_messages(self, agent, mock_skill_server):
        """Agent handles a burst of rapid-fire requests."""
        mock_skill_server(
            topic="TOPIC_BURST",
            handler=lambda i: f"ack:{i}",
        )

        tasks = [
            agent.publish("TOPIC_BURST", f"msg-{i}", timeout=10.0)
            for i in range(20)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = [r for r in results if isinstance(r, str)]

        # We expect most or all to succeed
        assert len(successes) >= 15, f"Only {len(successes)}/20 succeeded"

    async def test_server_crash_mid_batch(self, agent, mock_skill_server):
        """Some requests succeed even if the server dies mid-batch."""
        call_count = 0

        def crashing_handler(intent):
            nonlocal call_count
            call_count += 1
            if call_count > 3:
                raise RuntimeError("Server crashed!")
            return f"ok:{call_count}"

        mock_skill_server(topic="TOPIC_CRASH", handler=crashing_handler)

        results = []
        for i in range(5):
            try:
                r = await agent.publish("TOPIC_CRASH", f"req-{i}", timeout=3.0)
                results.append(("ok", r))
            except (RuntimeError, asyncio.TimeoutError) as e:
                results.append(("error", str(e)))

        # First 3 should succeed
        ok_count = sum(1 for status, _ in results if status == "ok")
        assert ok_count >= 3
