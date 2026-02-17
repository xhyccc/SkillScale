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

    # Test 1: Mode 1 — Explicit skill selection (JSON with skill name)
    print("\n[TEST 1] Mode 1: Explicit skill — text summarization...")
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

    # Test 2: Mode 1 — Explicit skill selection (CSV analyzer)
    print("\n[TEST 2] Mode 1: Explicit skill — CSV analysis...")
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

    # Test 3: Mode 2 — Task-based intent (server auto-matches skill)
    print("\n[TEST 3] Mode 2: Task-based — 'summarize this article'...")
    task_intent = json.dumps({
        "task": (
            "Please summarize the following article about climate change: "
            "Global temperatures have risen by 1.1°C since pre-industrial times. "
            "Ice caps are melting at accelerating rates. Sea levels have risen "
            "by 20cm in the last century. Extreme weather events are becoming "
            "more frequent. Scientists urge immediate action to reduce emissions."
        ),
    })

    try:
        result = await agent.publish("TOPIC_DATA_PROCESSING", task_intent)
        response = agent.respond(result)
        print(f"  ✅ SUCCESS! Response ({len(response)} chars):")
        print(f"  {response[:200]}...")
    except asyncio.TimeoutError:
        print("  ❌ TIMEOUT: No response from skill server")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")

    # Test 4: Mode 2 — Task-based with plain-text (no JSON wrapper)
    print("\n[TEST 4] Mode 2: Plain text — 'analyze this csv data'...")
    plain_intent = "analyze this csv data: product,price,qty\nWidget,29.99,100\nGadget,49.99,50\n"

    try:
        result = await agent.publish("TOPIC_DATA_PROCESSING", plain_intent)
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
