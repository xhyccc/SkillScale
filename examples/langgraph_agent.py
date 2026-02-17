"""
Example: LangGraph Skill Router with SkillScale

Demonstrates a LangGraph StateGraph that routes user queries through
the SkillScale ZeroMQ middleware to distributed C++ skill servers.

The graph flow:
    router → invoke_skill → format_output → END

Prerequisites:
    pip install langgraph skillscale
    # Start SkillScale services (proxy + skill server)
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skillscale import SkillScaleClient, ClientConfig
from skillscale.adapters.langgraph import SkillScaleGraph


async def main():
    config = ClientConfig(
        proxy_xsub="tcp://127.0.0.1:5444",
        proxy_xpub="tcp://127.0.0.1:5555",
    )
    client = SkillScaleClient(config)
    await client.connect()

    # Build the LangGraph from skill discovery
    sg = SkillScaleGraph.from_skills_dir(client, "./skills")

    print("Available skills:")
    for s in sg.get_available_skills():
        print(f"  - {s.name} ({s.topic}): {s.description}")
    print()

    # Build graph without LLM (uses keyword matching for routing)
    graph = sg.build_graph(llm=None)

    # Run queries through the graph
    queries = [
        "Summarize the following: AI is changing everything. Deep learning is powerful. NLP is advancing.",
        "Analyze this CSV: name,score\nAlice,92\nBob,85\nCharlie,97",
    ]

    for query in queries:
        print(f"Query: {query[:60]}...")
        result = await graph.ainvoke({"input": query})
        print(f"Output:\n{result.get('output', result.get('error', 'No output'))}\n")
        print("-" * 60)

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
