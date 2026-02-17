"""
SkillScale SDK — Middleware for ZeroMQ Skill-as-a-Service Infrastructure

Provides a framework-agnostic client for publishing intents to distributed
C++ Skill Servers and collecting responses. Includes pre-built adapters for
LangChain, LangGraph, CrewAI, and other agent frameworks.

Usage:
    from skillscale import SkillScaleClient, ClientConfig

    client = SkillScaleClient()
    await client.connect()
    result = await client.invoke("TOPIC_DATA_PROCESSING", "summarize this text")
    await client.close()
"""

from skillscale.client import SkillScaleClient, ClientConfig
from skillscale.discovery import SkillDiscovery, SkillMetadata, TopicMetadata

__version__ = "0.1.0"
__all__ = [
    "SkillScaleClient", "ClientConfig",
    "SkillDiscovery", "SkillMetadata", "TopicMetadata",
]
