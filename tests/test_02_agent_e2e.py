"""
Test 2: Agent ↔ Skill Server End-to-End

Validates the complete request-response cycle:
  1. Agent publishes intent to a topic
  2. Mock skill server receives and processes it
  3. Skill server publishes response on reply_to topic
  4. Agent receives and resolves the future
"""

import asyncio

import pytest
import pytest_asyncio


@pytest.mark.asyncio
class TestAgentSkillServerE2E:
    """Full round-trip tests through real ZeroMQ."""

    async def test_single_request_response(self, agent, mock_skill_server):
        """Basic: publish intent → receive response."""
        server = mock_skill_server(
            topic="TOPIC_E2E_BASIC",
            handler=lambda intent: f"Result for: {intent}",
        )

        result = await agent.publish("TOPIC_E2E_BASIC", "What is 2+2?")

        assert "Result for: What is 2+2?" == result
        assert len(server.requests_received) == 1
        assert server.requests_received[0]["intent"] == "What is 2+2?"

    async def test_multiple_sequential_requests(self, agent, mock_skill_server):
        """Sequential requests to the same topic."""
        call_count = 0

        def handler(intent):
            nonlocal call_count
            call_count += 1
            return f"Response #{call_count}"

        mock_skill_server(topic="TOPIC_SEQ", handler=handler)

        r1 = await agent.publish("TOPIC_SEQ", "first")
        r2 = await agent.publish("TOPIC_SEQ", "second")
        r3 = await agent.publish("TOPIC_SEQ", "third")

        assert r1 == "Response #1"
        assert r2 == "Response #2"
        assert r3 == "Response #3"
        assert call_count == 3

    async def test_parallel_requests_different_topics(self, agent, mock_skill_server):
        """Parallel requests to different skill topics."""
        mock_skill_server(
            topic="TOPIC_ALPHA",
            handler=lambda i: "alpha:" + i,
        )
        mock_skill_server(
            topic="TOPIC_BETA",
            handler=lambda i: "beta:" + i,
        )

        results = await agent.publish_parallel([
            ("TOPIC_ALPHA", "query_a"),
            ("TOPIC_BETA", "query_b"),
        ])

        assert results[0] == "alpha:query_a"
        assert results[1] == "beta:query_b"

    async def test_parallel_requests_same_topic(self, agent, mock_skill_server):
        """Multiple parallel requests to the same topic."""
        counter = 0

        def handler(intent):
            nonlocal counter
            counter += 1
            return f"handled:{intent}"

        mock_skill_server(topic="TOPIC_PARALLEL", handler=handler)

        results = await agent.publish_parallel([
            ("TOPIC_PARALLEL", "a"),
            ("TOPIC_PARALLEL", "b"),
            ("TOPIC_PARALLEL", "c"),
        ])

        assert all(isinstance(r, str) for r in results)
        assert counter == 3

    async def test_timeout_no_server(self, agent):
        """Request to nonexistent topic should timeout."""
        with pytest.raises(asyncio.TimeoutError):
            await agent.publish("TOPIC_NONEXISTENT", "hello?", timeout=1.5)

    async def test_skill_server_error_propagation(self, agent, mock_skill_server):
        """Skill server errors propagate as RuntimeError."""
        def failing_handler(intent):
            raise ValueError("Simulated skill failure")

        mock_skill_server(topic="TOPIC_FAIL", handler=failing_handler)

        with pytest.raises(RuntimeError, match="Simulated skill failure"):
            await agent.publish("TOPIC_FAIL", "trigger error")

    async def test_respond_tool(self, agent, mock_skill_server):
        """The respond() tool returns formatted content."""
        mock_skill_server(
            topic="TOPIC_RESPOND",
            handler=lambda i: "## Analysis\n\nResult is 42.",
        )

        result = await agent.publish("TOPIC_RESPOND", "analyze")
        response = agent.respond(result)

        assert "## Analysis" in response
        assert "42" in response

    async def test_large_payload(self, agent, mock_skill_server):
        """Large payloads survive the full round-trip."""
        large_text = "x" * 100_000

        mock_skill_server(
            topic="TOPIC_LARGE",
            handler=lambda i: f"got {len(i)} chars",
        )

        result = await agent.publish("TOPIC_LARGE", large_text)
        assert "100000" in result

    async def test_request_id_correlation(self, agent, mock_skill_server):
        """Each request gets a unique request_id and correct correlation."""
        mock_skill_server(
            topic="TOPIC_CORR",
            handler=lambda i: i.upper(),
        )

        r1 = await agent.publish("TOPIC_CORR", "hello")
        r2 = await agent.publish("TOPIC_CORR", "world")

        assert r1 == "HELLO"
        assert r2 == "WORLD"
