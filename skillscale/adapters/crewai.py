"""
SkillScale ↔ CrewAI Adapter

Wraps the SkillScale middleware as CrewAI-compatible tools so that
CrewAI Agents and Crews can invoke distributed C++ skill servers.

Usage:
    from skillscale import SkillScaleClient
    from skillscale.adapters.crewai import SkillScaleCrewTools

    client = SkillScaleClient()
    await client.connect()

    crew_tools = SkillScaleCrewTools.from_skills_dir(client, "./skills")
    tools = crew_tools.get_tools()

    # Use with CrewAI
    agent = Agent(role="analyst", tools=tools, ...)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Type

from skillscale.client import SkillScaleClient
from skillscale.discovery import SkillDiscovery, SkillMetadata

log = logging.getLogger("skillscale.crewai")

try:
    from crewai.tools import BaseTool as CrewBaseTool
    from pydantic import BaseModel, Field

    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False


def _check_crewai():
    if not HAS_CREWAI:
        raise ImportError(
            "CrewAI is required for this adapter. "
            "Install with: pip install crewai"
        )


# ──────────────────────────────────────────────────────────
#  Per-skill CrewAI tool
# ──────────────────────────────────────────────────────────
if HAS_CREWAI:

    class _SkillInput(BaseModel):
        intent: str = Field(description="The request or data to send to the skill.")

    class SkillScaleCrewTool(CrewBaseTool):
        """A CrewAI tool that invokes a single SkillScale skill."""

        name: str = ""
        description: str = ""
        args_schema: Type[BaseModel] = _SkillInput

        # Internal — not exposed to LLM
        client: Any = None
        skill_topic: str = ""
        skill_name: str = ""

        class Config:
            arbitrary_types_allowed = True

        def _run(self, intent: str) -> str:
            payload = json.dumps({"skill": self.skill_name, "data": intent})
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.client.invoke(self.skill_topic, payload)
                )
            except asyncio.TimeoutError:
                return f"Error: skill '{self.skill_name}' timed out."
            except RuntimeError as e:
                return f"Error: {e}"
            finally:
                loop.close()


# ──────────────────────────────────────────────────────────
#  Tool factory
# ──────────────────────────────────────────────────────────
class SkillScaleCrewTools:
    """
    Generates CrewAI tools from discovered SkillScale skills.

    Usage:
        crew_tools = SkillScaleCrewTools.from_skills_dir(client, "./skills")
        tools = crew_tools.get_tools()
    """

    def __init__(self, client: SkillScaleClient, discovery: SkillDiscovery):
        _check_crewai()
        self.client = client
        self.discovery = discovery

    @classmethod
    def from_skills_dir(
        cls, client: SkillScaleClient, skills_dir: str
    ) -> "SkillScaleCrewTools":
        disc = SkillDiscovery(skills_root=skills_dir).scan()
        return cls(client, disc)

    def get_tools(self) -> list:
        """Return one CrewAI tool per discovered skill."""
        tools = []
        for skill in self.discovery.list_skills():
            tool = SkillScaleCrewTool(
                name=skill.name,
                description=skill.to_tool_description(),
                client=self.client,
                skill_topic=skill.topic,
                skill_name=skill.name,
            )
            tools.append(tool)
        return tools

    def get_metadata_prompt(self) -> str:
        return self.discovery.metadata_summary()
