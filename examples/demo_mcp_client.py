"""
MCP Client Demo — Connects to SkillScale Gateway via Streamable HTTP (SSE).

The Gateway exposes MCP at http://localhost:8086/mcp.
No binary launch needed — just connect to the network port.

Demonstrates two invocation modes:
  1. Agent-level (coarse-grained):  agent__<category>
     → Skill Server reads AGENTS.md to auto-select the best skill.
  2. Skill-level (fine-grained):    <category>__<skill>
     → Directly invokes a specific skill, bypassing AGENTS.md routing.
"""
import asyncio
import json
import os
import sys
import textwrap

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "http://localhost:8086/mcp"
MCP_TIMEOUT = int(float(os.environ.get("SKILLSCALE_GATEWAY_TIMEOUT", "600")))

SAMPLE_CODE = textwrap.dedent("""\
    import os
    import sys
    import json
    import re          # unused

    THRESHOLD = 10

    def process_data(data):
        count = 0
        for item in data:
            if item > THRESHOLD:
                count += 1
                if count > 5:
                    return True
        return False

    def _helper():     # dead code — never called
        pass
""")


def _print_result(result):
    """Pretty-print an MCP CallToolResult."""
    if result.isError:
        print(f"  ✗ Error: {result.content[0].text}")
        return
    for part in result.content:
        # Indent multi-line text for readability
        lines = part.text.strip().splitlines()
        if len(lines) <= 3:
            print(f"  {part.text.strip()}")
        else:
            # Print first 20 lines, truncate if longer
            for line in lines[:20]:
                print(f"  {line}")
            if len(lines) > 20:
                print(f"  ... ({len(lines) - 20} more lines)")


async def main():
    print("=" * 60)
    print("  SkillScale MCP Client Demo")
    print("=" * 60)
    print(f"  Server:  {MCP_URL}")
    print(f"  Timeout: {MCP_TIMEOUT}s")
    print()

    async with streamablehttp_client(
        MCP_URL, timeout=MCP_TIMEOUT, sse_read_timeout=MCP_TIMEOUT
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── 1. List available tools ──────────────────────────────
            print("[1] Available MCP Tools")
            print("-" * 60)
            tools = await session.list_tools()
            agents, skills = [], []
            for t in tools.tools:
                bucket = agents if t.name.startswith("agent__") else skills
                bucket.append(t)
                print(f"  {'🤖' if t.name.startswith('agent__') else '🔧'} {t.name}")
            print(f"\n  Total: {len(agents)} agents, {len(skills)} skills")
            print()

            # ── 2. Agent-level call (coarse-grained) ─────────────────
            # The caller only says *which agent* to talk to.
            # The Skill Server reads AGENTS.md and auto-picks the best
            # skill for the input.
            print("[2] Agent-level invocation  (agent__code-analysis)")
            print("-" * 60)
            print("  Mode:  coarse-grained — AGENTS.md picks the skill")
            print("  Input: Python snippet with unused import + dead code")
            print()
            try:
                result = await session.call_tool(
                    "agent__code-analysis",
                    arguments={"input": SAMPLE_CODE},
                )
                _print_result(result)
            except Exception as e:
                print(f"  ✗ Agent call failed: {e}")
            print()

            # ── 3. Skill-level call (fine-grained) ───────────────────
            # The caller explicitly names the skill.
            # No AGENTS.md routing — goes straight to the executor.
            print("[3] Skill-level invocation  (code-analysis__dead-code-detector)")
            print("-" * 60)
            print("  Mode:  fine-grained — directly invoke dead-code-detector")
            print("  Input: same Python snippet")
            print()
            try:
                result = await session.call_tool(
                    "code-analysis__dead-code-detector",
                    arguments={"input": SAMPLE_CODE},
                )
                _print_result(result)
            except Exception as e:
                print(f"  ✗ Skill call failed: {e}")
            print()

            print("=" * 60)
            print("  MCP Demo Complete")
            print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())