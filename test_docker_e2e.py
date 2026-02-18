#!/usr/bin/env python3
"""Quick E2E test against containerized SkillScale services."""
import asyncio
import json
import os

os.environ['SKILLSCALE_PROXY_XSUB'] = 'tcp://localhost:5444'
os.environ['SKILLSCALE_PROXY_XPUB'] = 'tcp://localhost:5555'

from skillscale.client import SkillScaleClient


async def test():
    client = SkillScaleClient()
    await client.connect()

    # Explicit mode test
    print("=== Testing text-summarizer (Docker Container) ===")
    intent = json.dumps({
        "skill": "text-summarizer",
        "data": "Summarize this: Docker containers provide lightweight virtualization "
                "that packages applications with their dependencies. This enables "
                "consistent deployment across different environments."
    })
    result = await client.invoke(topic="TOPIC_DATA_PROCESSING", intent=intent)
    print(f"Result: {result[:300]}..." if len(result) > 300 else f"Result: {result}")

    await client.close()
    print("\nSUCCESS: Containerized skill server responded!")


if __name__ == "__main__":
    asyncio.run(test())
