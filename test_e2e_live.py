"""
End-to-end smoke test: Python agent → C++ Proxy → C++ Skill Server → skill execution → response.
This script sends a real request through the full stack.
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agent"))
from main import SkillScaleAgent, AgentConfig


async def run_e2e_test():
    config = AgentConfig(
        proxy_xsub="tcp://127.0.0.1:5444",
        proxy_xpub="tcp://127.0.0.1:5555",
        default_timeout=15.0,
    )

    agent = SkillScaleAgent(config)
    await agent.start()

    print("\n" + "=" * 60)
    print("  E2E TEST: Python Agent → C++ Proxy → C++ Skill Server")
    print("=" * 60)

    # Test 1: Send text to be summarized (with explicit skill name in JSON)
    print("\n[TEST 1] Sending text summarization request...")
    text_intent = json.dumps({
        "skill": "text-summarizer",
        "data": (
            "Machine learning is a branch of artificial intelligence. "
            "It focuses on building systems that learn from data. "
            "Deep learning is a subset of machine learning. "
            "It uses neural networks with many layers. "
            "Natural language processing enables computers to understand text. "
            "Computer vision allows machines to interpret images. "
            "Reinforcement learning trains agents through rewards. "
            "The field continues to advance rapidly each year."
        ),
    })

    try:
        result = await agent.publish("TOPIC_DATA_PROCESSING", text_intent)
        response = agent.respond(result)
        print(f"  ✅ SUCCESS! Response ({len(response)} chars):")
        print(f"  {response[:200]}...")
    except asyncio.TimeoutError:
        print("  ❌ TIMEOUT: No response from skill server")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")

    # Test 2: Send CSV data (with explicit skill name in JSON)
    print("\n[TEST 2] Sending CSV analysis request...")
    csv_intent = json.dumps({
        "skill": "csv-analyzer",
        "data": "name,age,score\nAlice,30,95.5\nBob,25,87.2\nCharlie,35,91.0\n",
    })

    try:
        result = await agent.publish("TOPIC_DATA_PROCESSING", csv_intent)
        response = agent.respond(result)
        print(f"  ✅ SUCCESS! Response ({len(response)} chars):")
        print(f"  {response[:200]}...")
    except asyncio.TimeoutError:
        print("  ❌ TIMEOUT: No response from skill server")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")

    print("\n" + "=" * 60)
    print("  E2E test complete!")
    print("=" * 60 + "\n")

    await agent.stop()


if __name__ == "__main__":
    asyncio.run(run_e2e_test())
