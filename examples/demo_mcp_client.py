import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    print("Starting MCP Client Demo...")
    
    # Run the Rust Gateway as an MCP Stdio Server
    server_env = os.environ.copy()
    server_env["SKILLSCALE_BROKER_URL"] = "localhost:9092"
    server_env["RUST_BACKTRACE"] = "1"
    
    # Path to compiled Rust binary
    rust_gateway_bin = os.path.abspath("skillscale-rs/target/release/gateway")
    
    if not os.path.exists(rust_gateway_bin):
        print(f"Error: Rust gateway binary not found at {rust_gateway_bin}")
        print("Did you run ./run.sh to compile it?")
        sys.exit(1)

    server_params = StdioServerParameters(
        command=rust_gateway_bin,
        args=["--mcp"],
        env=server_env
    )
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("Initializing MCP Session...")
                await session.initialize()
                
                await asyncio.sleep(2)
                
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
                        "agent__code-analysis",
                        arguments={
                            "input": "def process_data(data):\n    count = 0\n    for item in data:\n        if item > 10:\n            count += 1\n            if count > 5:\n                return True\n    return False\nPlease check this code's complexity."
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
        import traceback
        traceback.print_exc()
        print(f"Failed to connect or run demo: {e}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Hack to silence unclosed transport errors on quick exit
        pass