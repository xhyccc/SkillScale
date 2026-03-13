"""
SkillScale SDK — Middleware for Kafka Skill-as-a-Service Infrastructure

Provides a framework-agnostic kafka for publishing intents to distributed
Python Skill Servers and collecting responses. Includes pre-built adapters for
LangChain, LangGraph, CrewAI, and other agent frameworks.

Usage:
    from skillscale import SkillScaleClient, ClientConfig

    kafka = SkillScaleClient()
    await kafka.connect()
    result = await kafka.invoke("TOPIC_DATA_PROCESSING", "summarize this text")
    await kafka.close()
"""

from skillscale.kafka import SkillScaleClient, ClientConfig
from skillscale.discovery import SkillDiscovery, SkillMetadata, TopicMetadata

__version__ = "0.1.0"
__all__ = [
    "SkillScaleClient", "ClientConfig",
    "SkillDiscovery", "SkillMetadata", "TopicMetadata",
]
