import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    print("Starting MCP Client Demo...")
    
    # Run the gateway as an MCP Stdio Server
    server_env = os.environ.copy()
    server_env["SKILLSCALE_PROTOCOL_A2A"] = "0"
    server_env["SKILLSCALE_PROTOCOL_MCP"] = "1"
    
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["gateway/transparent_layer.py"],
        env=server_env
    )
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("Initializing MCP Session...")
                await session.initialize()
                
                print("\nFetching available MCP Tools...")
                tools = await session.list_tools()
                for t in tools.tools:
                    print(f" - Found Tool: {t.name} (description: {t.description})")
                
                print("\nFetching available MCP Resources (Contexts)...")
                resources = await session.list_resources()
                for r in resources.resources:
                    print(f" - Found Resource: {r.uri}")

                print("\nTest Tool Invocation:")
                try:
                    result = await session.call_tool(
                        "invoke_skill",
                        arguments={
                            "category": "CODE_ANALYSIS",
                            "skill_name": "code-complexity",
                            "payload": {"input": "def process_data(data):\n    count = 0\n    for item in data:\n        if item > 10:\n            count += 1\n            if count > 5:\n                return True\n    return False"}
                        }
                    )
                    print(f" - Tool Result: {result}")
                except Exception as e:
                    print(f" - Tool execution raised (expected if ZMQ proxy/server is offline): {e}")

                print("\nTest Resource Read (Context Sync):")
                try:
                    result = await session.read_resource("skillscale://context/session_123")
                    print(f" - Resource Content: {result.contents[0].text}")
                except Exception as e:
                    print(f" - Resource read raised (expected if ZMQ proxy/server is offline): {e}")
                    
    except Exception as e:
        print(f"Failed to connect or run demo: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())