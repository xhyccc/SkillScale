"""
Example: LangChain ReAct Agent with SkillScale

Demonstrates wiring the SkillScale middleware into a LangChain agent.
The agent uses OpenAI function calling to route user queries to the
appropriate distributed C++ skill server via ZeroMQ.

Prerequisites:
    pip install langchain langchain-openai skillscale
    export OPENAI_API_KEY=sk-...

    # Start SkillScale services:
    ./proxy/build/skillscale_proxy
    ./skill-server/build/skillscale_skill_server --topic TOPIC_DATA_PROCESSING --skills-dir ./skills/data-processing
"""

import asyncio
import os
import sys

# Ensure skillscale package is importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skillscale import SkillScaleClient, ClientConfig
from skillscale.adapters.langchain import SkillScaleToolkit


async def main():
    # ── Connect to the SkillScale middleware ──
    config = ClientConfig(
        proxy_xsub="tcp://127.0.0.1:5444",
        proxy_xpub="tcp://127.0.0.1:5555",
    )
    client = SkillScaleClient(config)
    await client.connect()

    # ── Build LangChain tools from skill discovery ──
    toolkit = SkillScaleToolkit.from_skills_dir(client, "./skills")
    tools = toolkit.get_tools()

    print(f"Discovered {len(tools)} skill tools:")
    for t in tools:
        print(f"  - {t.name}: {t.description}")
    print()

    # ── Option A: Use tools directly (no LLM required) ──
    print("=== Direct tool invocation (no LLM) ===")
    for tool in tools:
        if "summariz" in tool.name:
            result = await tool.ainvoke({"intent": "AI is transforming the world. Machine learning enables new capabilities. Deep learning pushes boundaries further."})
            print(f"[{tool.name}] Result:\n{result}\n")
            break

    # ── Option B: Wire into a LangChain agent (requires OpenAI key) ──
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            from langchain_openai import ChatOpenAI
            from langchain.agents import create_openai_functions_agent, AgentExecutor
            from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

            prompt = ChatPromptTemplate.from_messages([
                ("system",
                 "You are an assistant with access to distributed skill servers.\n"
                 f"{toolkit.get_metadata_prompt()}\n"
                 "Use the available tools to answer the user's question."),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ])

            agent = create_openai_functions_agent(llm, tools, prompt)
            executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

            print("=== LangChain Agent with OpenAI ===")
            result = await executor.ainvoke({
                "input": "Summarize this: Neural networks learn patterns from data. "
                         "CNNs excel at image tasks. RNNs handle sequences."
            })
            print(f"Agent output: {result['output']}\n")
        except ImportError as e:
            print(f"Skipping LLM agent (missing dep): {e}")
    else:
        print("Set OPENAI_API_KEY to test the full LangChain agent flow.\n")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
