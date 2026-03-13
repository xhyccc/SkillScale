import asyncio
import json
import logging
import uuid
import os
import sys
from typing import Any, Dict, Optional

# import MCP (Model Context Protocol) SDKs (e.g. mcp, fastmcp)
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None

# Assuming skillscale package is in PYTHONPATH
from skillscale.kafka import SkillScaleClient, ClientConfig
from skillscale.kafka import SkillScaleKafkaClient, KafkaConfig

log = logging.getLogger("skillscale.gateway")
logging.basicConfig(level=logging.INFO)

class SkillScaleMCPBridge:
    """
    Bridge between Model Context Protocol (MCP) clients and SkillScale Kafka/Redpanda backend.
    
    This server exposes SkillScale skills as MCP tools.
    """

    def __init__(self):
        self.backend_type = os.getenv("SKILLSCALE_BACKEND", "kafka").lower()
        if self.backend_type in ["kafka", "redpanda"]:
            self.client = SkillScaleKafkaClient()
            log.info("Using Kafka/Redpanda backend")
        else:
            self.client = SkillScaleClient(ClientConfig.from_env())
            log.info("Using ZeroMQ backend")
        
        self.gateway_timeout = float(os.getenv("SKILLSCALE_GATEWAY_TIMEOUT", "180.0"))
        
        if not FastMCP:
             raise ImportError("mcp[cli] or fastmcp is not installed. Please run `pip install mcp`")

        self.mcp = FastMCP("SkillScaleMCPGateway")
        self.setup_mcp_tools()

    async def start(self):
        """Starts the gateway services."""
        await self.client.connect()
        log.info(f"{type(self.client).__name__} connected.")
        
        log.info("Starting MCP Server (Stdio)...")
        # Start Stdio server for MCP clients
        await self.mcp.run_stdio_async()

    def setup_mcp_tools(self):
        """Register MCP tools that dynamically route to Kafka topics/skills."""

        @self.mcp.tool()
        async def invoke_skill(category: str, skill_name: str, payload: Dict[str, Any]) -> str:
            """
            Expose internal skills to MCP clients.
            This routes an MCP tool call transparently over Kafka/ZMQ to the corresponding Skill Server.
            """
            topic = f"TOPIC_{category.upper().replace('-', '_')}"
            
            # Pack MCP payload AND CONTEXT into SkillScale format
            zmq_payload = {
                "skill": skill_name,
                "data": payload,
                "context": {
                    "session_id": "mcp_session_xxx",  # Placeholder
                    "protocol": "mcp/1.0"
                },
                "metadata": {"source": "mcp-gateway"}
            }
            
            log.info(f"[MCP] Routing tool '{skill_name}' to topic '{topic}'")
            
            # Send through the bus
            reply = await self.client.invoke(topic, json.dumps(zmq_payload), timeout=self.gateway_timeout)
            return json.dumps(reply)

        @self.mcp.resource("skillscale://context/{session_id}")
        async def get_shared_context(session_id: str) -> str:
            """
            Expose internal shared state/context as an MCP Resource.
            """
            log.info(f"[MCP] Client requested shared context for session {session_id}")
            # Ask the bus for the latest state of this session
            zmq_payload = {"action": "get_state", "session_id": session_id}
            reply = await self.client.invoke("TOPIC_CONTEXT_SYNC", json.dumps(zmq_payload), timeout=5.0)
            return json.dumps(reply)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    bridge = SkillScaleMCPBridge()
    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        pass
