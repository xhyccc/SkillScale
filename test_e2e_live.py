"""
End-to-end smoke test: Python agent → C++ Proxy → Python Skill Server → skill execution → response.

This test demonstrates the full LLM-powered OpenSkills workflow:
  1. SkillDiscovery scans the skills/ directory to auto-discover topics, their
     descriptions, and per-skill metadata (OpenSkills SKILL.md format).
  2. An LLM-based router uses the metadata_summary() as system-prompt context
     to classify each user intent and route it to the correct ZMQ topic.
  3. Intents are sent as task-based (Mode 2) — the Python skill server uses
     LLM + per-server AGENTS.md to match the best installed skill.
  4. Skill scripts call the LLM for intelligent analysis (summarization,
     CSV insights, complexity review, dead-code suggestions).

Two skill servers run (Python, containerised):
  - TOPIC_DATA_PROCESSING  (text-summarizer, csv-analyzer)
  - TOPIC_CODE_ANALYSIS    (code-complexity, dead-code-detector)
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from agent.main import SkillScaleAgent, AgentConfig
from skillscale.discovery import SkillDiscovery, TopicMetadata

# LLM utilities for routing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "skills"))
from llm_utils import chat as llm_chat, get_provider_info


# ──────────────────────────────────────────────────────────
#  LLM-based topic router
# ──────────────────────────────────────────────────────────
ROUTER_SYSTEM_PROMPT = """\
You are a routing classifier for a distributed skill system.

You will be given:
1. A list of available topics with their descriptions and skills.
2. A user intent (task description).

Your job is to decide which TOPIC the intent should be routed to.

Rules:
- Reply with ONLY the topic name (e.g., TOPIC_DATA_PROCESSING).
- Do NOT include any explanation, punctuation, or extra text.
- Choose the single best-matching topic based on the intent.
"""


def route_to_topic_llm(
    user_intent: str,
    topics: list[TopicMetadata],
    metadata_summary: str,
) -> str:
    """
    Use the LLM to classify a user intent and route it to the
    correct topic, using the auto-discovered metadata as context.
    """
    topic_names = [tm.topic for tm in topics]
    user_msg = (
        f"Available topics and skills:\n{metadata_summary}\n\n"
        f"Valid topic names: {', '.join(topic_names)}\n\n"
        f"User intent: {user_intent}\n\n"
        f"Which topic should this intent be routed to? "
        f"Reply with ONLY the topic name."
    )

    reply = llm_chat(
        ROUTER_SYSTEM_PROMPT,
        user_msg,
        max_tokens=1024,
        temperature=0.0,
    ).strip()

    # Sanitize: extract the topic name from the reply
    for tn in topic_names:
        if tn in reply:
            return tn

    # Fallback: return the raw reply (might still match)
    return reply


async def run_e2e_test():
    # ── Discovery: auto-scan skills directory ──────────────
    discovery = SkillDiscovery(
        skills_root="./skills",
        topic_descriptions={
            "TOPIC_DATA_PROCESSING": (
                "Data processing server — text summarization, CSV analysis, "
                "and general data transformation"
            ),
            "TOPIC_CODE_ANALYSIS": (
                "Code analysis server — cyclomatic complexity metrics, "
                "dead code detection, and Python static analysis"
            ),
        },
    ).scan()

    print("\n" + "=" * 70)
    print("  SKILL DISCOVERY — auto-discovered topics and skills")
    print("=" * 70)
    summary = discovery.metadata_summary()
    print(summary)

    # Show LLM provider info
    info = get_provider_info()
    print(f"  LLM Provider: {info['provider']} ({info['model']})")
    print()

    topics = discovery.list_topic_metadata()
    assert len(topics) >= 2, f"Expected ≥2 topics, found {len(topics)}"

    # ── Connect the agent ──────────────────────────────────
    config = AgentConfig(
        proxy_xsub="tcp://127.0.0.1:5444",
        proxy_xpub="tcp://127.0.0.1:5555",
        default_timeout=30.0,
    )
    agent = SkillScaleAgent(config)
    await agent.start()

    print("=" * 70)
    print("  E2E TEST: LLM-powered agent routes intents via skill descriptions")
    print("=" * 70)

    # ── Test cases: each has a user intent and an expected topic ──
    test_cases = [
        {
            "name": "Summarize an article (→ data-processing)",
            "intent": {
                "task": (
                    "Summarize this article about quantum computing: "
                    "Quantum computing leverages quantum bits (qubits) to process "
                    "information exponentially faster than classical computers. "
                    "Google achieved quantum supremacy in 2019. IBM and Microsoft "
                    "are investing heavily in quantum hardware. Applications range "
                    "from drug discovery to cryptography."
                ),
            },
            "expected_topic": "TOPIC_DATA_PROCESSING",
        },
        {
            "name": "Analyze CSV data (→ data-processing)",
            "intent": {
                "task": "Analyze this CSV data and give me statistics",
                "data": (
                    "city,population,area_km2\n"
                    "Tokyo,13960000,2194\n"
                    "Delhi,11030000,1484\n"
                    "Shanghai,24870000,6341\n"
                    "Sao Paulo,12330000,1521\n"
                    "Mumbai,12440000,603\n"
                ),
            },
            "expected_topic": "TOPIC_DATA_PROCESSING",
        },
        {
            "name": "Measure code complexity (→ code-analysis)",
            "intent": {
                "task": "Analyze the cyclomatic complexity of this Python code",
                "data": (
                    "def fibonacci(n):\n"
                    "    if n <= 0:\n"
                    "        return 0\n"
                    "    elif n == 1:\n"
                    "        return 1\n"
                    "    else:\n"
                    "        return fibonacci(n-1) + fibonacci(n-2)\n"
                    "\n"
                    "def factorial(n):\n"
                    "    result = 1\n"
                    "    for i in range(2, n+1):\n"
                    "        result *= i\n"
                    "    return result\n"
                ),
            },
            "expected_topic": "TOPIC_CODE_ANALYSIS",
        },
        {
            "name": "Detect dead code (→ code-analysis)",
            "intent": {
                "task": "Find dead code and unused imports in this Python source",
                "data": (
                    "import os\n"
                    "import sys\n"
                    "import json\n"
                    "\n"
                    "def process(data):\n"
                    "    temp = 42\n"
                    "    result = len(data)\n"
                    "    return result\n"
                    "    print('done')  # unreachable\n"
                    "\n"
                    "def placeholder():\n"
                    "    pass\n"
                ),
            },
            "expected_topic": "TOPIC_CODE_ANALYSIS",
        },
    ]

    passed = 0
    failed = 0

    for i, tc in enumerate(test_cases, 1):
        print(f"\n[TEST {i}] {tc['name']}")
        intent_dict = tc["intent"]

        # Step 1: LLM Route — agent uses metadata + LLM to pick topic
        routing_text = intent_dict.get("task", json.dumps(intent_dict))
        chosen_topic = route_to_topic_llm(routing_text, topics, summary)
        expected = tc["expected_topic"]
        route_ok = chosen_topic == expected
        route_icon = "+" if route_ok else "FAIL"
        print(f"  [{route_icon}] Routed to: {chosen_topic}"
              f"{'' if route_ok else f' (expected {expected})'}")

        if not route_ok:
            failed += 1
            continue

        # Step 2: Publish task-based intent (Mode 2) to the chosen topic
        task_payload = json.dumps(intent_dict)
        try:
            result = await agent.publish(chosen_topic, task_payload)
            response = agent.respond(result)
            print(f"  [+] Response ({len(response)} chars):")
            for line in response.split("\n")[:6]:
                print(f"     {line}")
            if response.count("\n") > 6:
                print(f"     ... ({response.count(chr(10)) - 6} more lines)")
            passed += 1
        except asyncio.TimeoutError:
            print("  [FAIL] TIMEOUT: No response from skill server")
            failed += 1
        except Exception as e:
            print(f"  [FAIL] ERROR: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"  E2E RESULTS: {passed} passed, {failed} failed "
          f"({passed + failed} total)")
    print("=" * 70 + "\n")

    await agent.stop()

    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(run_e2e_test())
    sys.exit(0 if ok else 1)
