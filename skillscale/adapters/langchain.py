"""
SkillScale ↔ LangChain Adapter

Exposes the SkillScale middleware as LangChain-compatible tools so any
LangChain agent (OpenAI functions, ReAct, etc.) can invoke distributed
C++ skill servers through the ZeroMQ bus.

Usage:
    from skillscale import SkillScaleClient, ClientConfig
    from skillscale.adapters.langchain import SkillScaleToolkit

    client = SkillScaleClient()
    await client.connect()

    toolkit = SkillScaleToolkit.from_skills_dir(client, "./skills")
    tools = toolkit.get_tools()

    # Use with any LangChain agent
    agent = create_react_agent(llm, tools, prompt)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field

from skillscale.client import SkillScaleClient
from skillscale.discovery import SkillDiscovery, SkillMetadata

log = logging.getLogger("skillscale.langchain")

try:
    from langchain_core.tools import BaseTool, ToolException
    from langchain_core.callbacks import (
        AsyncCallbackManagerForToolRun,
        CallbackManagerForToolRun,
    )

    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False


def _check_langchain():
    if not HAS_LANGCHAIN:
        raise ImportError(
            "LangChain is required for this adapter. "
            "Install with: pip install langchain-core"
        )


# ──────────────────────────────────────────────────────────
#  Input schemas (Pydantic models for LangChain tool args)
# ──────────────────────────────────────────────────────────
class SkillInvokeInput(BaseModel):
    """Input schema for invoking a specific skill."""

    intent: str = Field(
        description="The user's request or data to send to the skill server."
    )


class TopicPublishInput(BaseModel):
    """Input schema for publishing to a raw ZMQ topic."""

    topic: str = Field(
        description="The ZeroMQ topic to publish to (e.g. TOPIC_DATA_PROCESSING)."
    )
    intent: str = Field(
        description="The user's request or data to send to the skill server."
    )


# ──────────────────────────────────────────────────────────
#  Per-Skill Tool — one tool per discovered skill
# ──────────────────────────────────────────────────────────
if HAS_LANGCHAIN:

    class SkillScaleTool(BaseTool):
        """
        A LangChain tool that invokes a single SkillScale skill through
        the ZeroMQ middleware.
        """

        name: str = ""
        description: str = ""
        args_schema: Type[BaseModel] = SkillInvokeInput
        handle_tool_error: bool = True

        # SkillScale internals (not exposed to LLM)
        client: Any = None       # SkillScaleClient
        skill_topic: str = ""
        skill_name: str = ""

        class Config:
            arbitrary_types_allowed = True

        def _run(
            self,
            intent: str,
            run_manager: Optional[CallbackManagerForToolRun] = None,
        ) -> str:
            """Synchronous wrapper (runs the async invoke in a new loop)."""
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._arun(intent))
            finally:
                loop.close()

        async def _arun(
            self,
            intent: str,
            run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        ) -> str:
            """Publish the intent to the skill's topic and return the result."""
            payload = json.dumps({"skill": self.skill_name, "data": intent})
            try:
                return await self.client.invoke(self.skill_topic, payload)
            except asyncio.TimeoutError:
                raise ToolException(
                    f"Timeout: skill '{self.skill_name}' did not respond."
                )
            except RuntimeError as e:
                raise ToolException(str(e))

    # ──────────────────────────────────────────────────────
    #  Generic Topic Tool — publishes to any topic
    # ──────────────────────────────────────────────────────

    class SkillScaleTopicTool(BaseTool):
        """
        A generic LangChain tool that publishes to any SkillScale topic.
        Useful when the LLM should choose the topic dynamically.
        """

        name: str = "skillscale_publish"
        description: str = (
            "Publish a request to a SkillScale distributed skill server. "
            "Requires a topic (e.g. TOPIC_DATA_PROCESSING) and an intent string."
        )
        args_schema: Type[BaseModel] = TopicPublishInput
        handle_tool_error: bool = True

        client: Any = None

        class Config:
            arbitrary_types_allowed = True

        def _run(
            self,
            topic: str,
            intent: str,
            run_manager: Optional[CallbackManagerForToolRun] = None,
        ) -> str:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._arun(topic, intent))
            finally:
                loop.close()

        async def _arun(
            self,
            topic: str,
            intent: str,
            run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
        ) -> str:
            try:
                return await self.client.invoke(topic, intent)
            except asyncio.TimeoutError:
                raise ToolException(
                    f"Timeout: no skill server responded on topic '{topic}'."
                )
            except RuntimeError as e:
                raise ToolException(str(e))


# ──────────────────────────────────────────────────────────
#  Toolkit — bundles all discovered skills into LangChain tools
# ──────────────────────────────────────────────────────────
class SkillScaleToolkit:
    """
    Generates LangChain tools from discovered SkillScale skills.

    Usage:
        toolkit = SkillScaleToolkit.from_skills_dir(client, "./skills")
        tools = toolkit.get_tools()          # one tool per skill
        tools = toolkit.get_topic_tool()     # single generic tool
    """

    def __init__(
        self,
        client: SkillScaleClient,
        discovery: SkillDiscovery,
    ):
        _check_langchain()
        self.client = client
        self.discovery = discovery

    @classmethod
    def from_skills_dir(
        cls, client: SkillScaleClient, skills_dir: str
    ) -> "SkillScaleToolkit":
        disc = SkillDiscovery(skills_root=skills_dir).scan()
        return cls(client, disc)

    def get_tools(self) -> list:
        """Return one LangChain Tool per discovered skill."""
        tools = []
        for skill in self.discovery.list_skills():
            tool = SkillScaleTool(
                name=skill.name,
                description=skill.to_tool_description(),
                client=self.client,
                skill_topic=skill.topic,
                skill_name=skill.name,
            )
            tools.append(tool)
        return tools

    def get_topic_tool(self) -> "SkillScaleTopicTool":
        """Return a single generic tool that publishes to any topic."""
        summary = self.discovery.metadata_summary()
        return SkillScaleTopicTool(
            client=self.client,
            description=(
                "Publish a request to a SkillScale distributed skill server. "
                f"Available skills:\n{summary}"
            ),
        )

    def get_metadata_prompt(self) -> str:
        """
        Return a system-prompt fragment listing all available skills
        (progressive disclosure — metadata layer only).
        """
        return self.discovery.metadata_summary()
