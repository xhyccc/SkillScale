"""
Example: Direct SkillScale SDK usage (no framework)

Shows how to use the SkillScale middleware client directly without
any agent framework. Useful for scripts, APIs, and custom agents.

Prerequisites:
    pip install skillscale    # or install from repo
    # Start SkillScale services (proxy + skill server)
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skillscale import SkillScaleClient, ClientConfig, SkillDiscovery


async def main():
    # ── Discover available skills ──
    discovery = SkillDiscovery(skills_root="./skills").scan()
    print("Discovered skills:")
    print(discovery.metadata_summary())

    # ── Connect middleware client ──
    async with SkillScaleClient() as client:
        # Single invocation
        print("=== Single Request ===")
        intent = json.dumps({
            "skill": "text-summarizer",
            "data": "AI is transforming industries. Machine learning is key. Deep learning leads.",
        })
        result = await client.invoke("TOPIC_DATA_PROCESSING", intent)
        print(f"Result:\n{result}\n")

        # Parallel invocations
        print("=== Parallel Requests ===")
        requests = [
            ("TOPIC_DATA_PROCESSING", json.dumps({
                "skill": "text-summarizer",
                "data": "Quantum computing promises breakthroughs. Qubits are fundamental. Error correction is crucial.",
            })),
            ("TOPIC_DATA_PROCESSING", json.dumps({
                "skill": "csv-analyzer",
                "data": "name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago\n",
            })),
        ]
        results = await client.invoke_parallel(requests)
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"Request {i + 1}: ERROR — {r}")
            else:
                print(f"Request {i + 1}:\n{r}\n")


if __name__ == "__main__":
    asyncio.run(main())
