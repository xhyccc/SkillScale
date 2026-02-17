"""
End-to-end smoke test: Python agent → C++ Proxy → C++ Skill Server → skill execution → response.

This test demonstrates the full middleware workflow:
  1. SkillDiscovery scans the skills/ directory to auto-discover topics, their
     descriptions, and per-skill metadata.
  2. A simple keyword router uses topic+skill descriptions to decide which
     ZMQ topic to publish each user intent to.
  3. Intents are sent as task-based (Mode 2) — the C++ skill server matches
     the best installed skill automatically.

Two skill servers run:
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


# ──────────────────────────────────────────────────────────
#  Simple keyword-based topic router (simulates what an LLM
#  agent would do using the auto-discovered descriptions)
# ──────────────────────────────────────────────────────────
def _simple_stem(word: str) -> str:
    """Reduce a word to a crude stem by stripping common suffixes."""
    for suffix in ("ization", "isation", "izing", "ising", "tion", "sion",
                   "ment", "ness", "izes", "ises", "ized", "ised",
                   "ize", "ise",
                   "ling", "ting", "ing", "able", "ible",
                   "ies", "ves", "ses", "ers", "ors",
                   "ed", "ly", "es", "er", "or", "al", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def _prefix_len(a: str, b: str) -> int:
    """Return the length of the common prefix between two strings."""
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def route_to_topic(
    user_intent: str,
    topics: list[TopicMetadata],
) -> str:
    """
    Pick the best topic for a user intent using prefix-similarity
    scoring against topic descriptions + skill names/descriptions.

    Scoring considers *all* (intent-word, pool-word) pairs:
      - Exact match            → +10  (handles short words like "csv")
      - Common prefix ≥ 5 chars → +prefix_len  (morphological variants)

    This naturally gives higher scores to topics whose descriptions
    contain more words resembling the user intent.

    In a real agent this would be an LLM call with the topic
    descriptions injected into the system prompt.
    """
    import re
    # Common / ambiguous words to ignore
    stopwords = {
        "the", "and", "for", "with", "from", "this", "that", "are", "was",
        "has", "have", "been", "will", "can", "not", "but", "its", "per",
        "all", "each", "any", "other", "into", "only", "also", "via",
        "server", "reports", "about", "using", "based", "such",
    }

    intent_words = set(re.findall(r'[a-z]+', user_intent.lower())) - stopwords
    intent_words = {w for w in intent_words if len(w) > 2}

    best_topic = topics[0].topic if topics else "TOPIC_DEFAULT"
    best_score = -1

    for tm in topics:
        # Build keyword pool from topic description + skill metadata
        pool_text = tm.description + " " + tm.topic.replace("_", " ")
        for skill in tm.skills:
            pool_text += " " + skill.name.replace("-", " ")
            pool_text += " " + skill.description
        pool_words = set(re.findall(r'[a-z]+', pool_text.lower())) - stopwords
        pool_words = {w for w in pool_words if len(w) > 2}

        # Score every (intent-word, pool-word) pair
        score = 0
        for iw in intent_words:
            for pw in pool_words:
                if iw == pw:
                    score += 10          # exact match
                elif _prefix_len(iw, pw) >= 5:
                    score += _prefix_len(iw, pw)  # morphological similarity

        if score > best_score:
            best_score = score
            best_topic = tm.topic

    return best_topic


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
    print(discovery.metadata_summary())

    topics = discovery.list_topic_metadata()
    assert len(topics) >= 2, f"Expected ≥2 topics, found {len(topics)}"

    # ── Connect the agent ──────────────────────────────────
    config = AgentConfig(
        proxy_xsub="tcp://127.0.0.1:5444",
        proxy_xpub="tcp://127.0.0.1:5555",
        default_timeout=15.0,
    )
    agent = SkillScaleAgent(config)
    await agent.start()

    print("=" * 70)
    print("  E2E TEST: Agent auto-routes intents to topics via descriptions")
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

        # Step 1: Route — agent picks topic using auto-discovered descriptions
        # Use the "task" field (or full intent text) for routing
        routing_text = intent_dict.get("task", json.dumps(intent_dict))
        chosen_topic = route_to_topic(routing_text, topics)
        expected = tc["expected_topic"]
        route_ok = chosen_topic == expected
        route_icon = "✅" if route_ok else "❌"
        print(f"  {route_icon} Routed to: {chosen_topic}"
              f"{'' if route_ok else f' (expected {expected})'}")

        if not route_ok:
            failed += 1
            continue

        # Step 2: Publish task-based intent (Mode 2) to the chosen topic
        task_payload = json.dumps(intent_dict)
        try:
            result = await agent.publish(chosen_topic, task_payload)
            response = agent.respond(result)
            print(f"  ✅ Response ({len(response)} chars):")
            for line in response.split("\n")[:6]:
                print(f"     {line}")
            if response.count("\n") > 6:
                print(f"     ... ({response.count(chr(10)) - 6} more lines)")
            passed += 1
        except asyncio.TimeoutError:
            print("  ❌ TIMEOUT: No response from skill server")
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
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
