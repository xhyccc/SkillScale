#!/usr/bin/env python3
"""
SkillScale Chat UI — FastAPI Backend

Provides a conversational testing interface that:
1. Uses SkillDiscovery to auto-discover available topics/skills.
2. Routes user messages to the correct topic via LLM.
3. Sends the intent to the skill server through ZMQ (via SkillScaleAgent).
4. Returns the skill response as a chat message.

Runs on port 8402 by default.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Project paths ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

from agent.main import SkillScaleAgent, AgentConfig
from skillscale.discovery import SkillDiscovery, TopicMetadata
from llm_utils import chat as llm_chat, get_provider_info

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

app = FastAPI(title="SkillScale Chat", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ──
_agent: Optional[SkillScaleAgent] = None
_discovery: Optional[SkillDiscovery] = None
_agent_lock = asyncio.Lock()


# ── LLM Topic Router ──
ROUTER_SYSTEM_PROMPT = """\
You are a routing classifier for a distributed skill system.

You will be given:
1. A list of available topics with their descriptions and skills.
2. A user intent (task description).

Your job is to decide which TOPIC the intent should be routed to.

Rules:
- Reply with ONLY the topic name (e.g., TOPIC_DATA_PROCESSING).
- Do NOT include any explanation, punctuation, or extra text.
- Choose the single best-matching topic based on the intent.
"""


def route_to_topic_llm(
    user_intent: str,
    topics: list[TopicMetadata],
    metadata_summary: str,
) -> str:
    """Use LLM to classify a user intent and route to correct topic."""
    topic_names = [tm.topic for tm in topics]
    user_msg = (
        f"Available topics and skills:\n{metadata_summary}\n\n"
        f"Valid topic names: {', '.join(topic_names)}\n\n"
        f"User intent: {user_intent}\n\n"
        f"Which topic should this intent be routed to? "
        f"Reply with ONLY the topic name."
    )

    reply = llm_chat(
        ROUTER_SYSTEM_PROMPT,
        user_msg,
        max_tokens=1024,
        temperature=0.0,
    ).strip()

    for tn in topic_names:
        if tn in reply:
            return tn

    # Fallback to first topic
    return topic_names[0] if topic_names else ""


# ── Helpers ──
def _get_discovery() -> SkillDiscovery:
    global _discovery
    if _discovery is None:
        skills_root = str(PROJECT_ROOT / "skills")
        _discovery = SkillDiscovery(
            skills_root=skills_root,
            topic_descriptions={
                "TOPIC_DATA_PROCESSING": "Data processing and analysis skills",
                "TOPIC_CODE_ANALYSIS": "Code analysis and review skills",
            },
        ).scan()
    return _discovery


async def _get_agent() -> SkillScaleAgent:
    global _agent
    async with _agent_lock:
        if _agent is None:
            config = AgentConfig.from_env()
            _agent = SkillScaleAgent(config)
            await _agent.start()
        return _agent


# ── API Models ──
class ChatRequest(BaseModel):
    message: str
    topic: Optional[str] = None  # if None, auto-route via LLM
    timeout: float = 180.0


class ChatResponse(BaseModel):
    id: str
    role: str = "assistant"
    message: str
    topic: str
    skill: str = ""
    elapsed_ms: float = 0
    routed_by: str = "llm"


class ConfigUpdate(BaseModel):
    provider: Optional[str] = None
    timeout: Optional[float] = None


# ── API Routes ──

@app.get("/api/config")
def get_config():
    """Current LLM and system configuration."""
    provider_info = get_provider_info()
    discovery = _get_discovery()
    return {
        "llm_provider": provider_info.get("provider", "unknown"),
        "llm_model": provider_info.get("model", "unknown"),
        "topics": [
            {
                "topic": tm.topic,
                "description": tm.description,
                "skills": [{"name": s.name, "description": s.description} for s in tm.skills],
            }
            for tm in discovery.list_topic_metadata()
        ],
        "metadata_summary": discovery.metadata_summary(),
    }


@app.get("/api/topics")
def list_topics():
    """List available topics and their skills."""
    discovery = _get_discovery()
    return {
        "topics": [
            {
                "topic": tm.topic,
                "description": tm.description,
                "skills": [{"name": s.name, "description": s.description} for s in tm.skills],
            }
            for tm in discovery.list_topic_metadata()
        ]
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Send a message through the SkillScale pipeline.

    1. Route to topic (LLM or manual override)
    2. Publish intent to skill server via ZMQ
    3. Return skill response
    """
    discovery = _get_discovery()
    topics = discovery.list_topic_metadata()

    if not topics:
        raise HTTPException(503, "No topics discovered. Are skills installed?")

    # Route
    if req.topic:
        topic = req.topic
        routed_by = "manual"
    else:
        topic = route_to_topic_llm(
            req.message,
            topics,
            discovery.metadata_summary(),
        )
        routed_by = "llm"

    if not topic:
        raise HTTPException(400, "Could not determine topic for this message.")

    # Invoke skill
    agent = await _get_agent()
    start = time.time()

    try:
        result = await agent.publish(topic, req.message, timeout=req.timeout)
        elapsed = (time.time() - start) * 1000
    except asyncio.TimeoutError:
        raise HTTPException(
            504,
            f"Skill server for {topic} did not respond within {req.timeout}s. "
            "Is the skill server running?"
        )
    except RuntimeError as e:
        raise HTTPException(502, f"Skill server error: {e}")

    return ChatResponse(
        id=uuid.uuid4().hex[:12],
        message=result,
        topic=topic,
        elapsed_ms=round(elapsed, 1),
        routed_by=routed_by,
    )


@app.post("/api/discovery/refresh")
def refresh_discovery():
    """Re-scan the skills directory."""
    global _discovery
    _discovery = None
    discovery = _get_discovery()
    return {
        "topics": discovery.list_topics(),
        "total_skills": len(discovery.list_skills()),
    }


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket endpoint for real-time chat.

    Client sends JSON: {"message": "...", "topic": "..." (optional)}
    Server responds JSON: {"id": "...", "role": "assistant", "message": "...", ...}
    """
    await websocket.accept()
    discovery = _get_discovery()
    agent = await _get_agent()

    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message", "").strip()
            manual_topic = data.get("topic")

            if not message:
                await websocket.send_json({"error": "Empty message"})
                continue

            topics = discovery.list_topic_metadata()

            if manual_topic:
                topic = manual_topic
                routed_by = "manual"
            else:
                topic = route_to_topic_llm(
                    message, topics, discovery.metadata_summary()
                )
                routed_by = "llm"

            # Send routing info
            await websocket.send_json({
                "type": "routing",
                "topic": topic,
                "routed_by": routed_by,
            })

            start = time.time()
            try:
                result = await agent.publish(topic, message, timeout=180.0)
                elapsed = (time.time() - start) * 1000

                await websocket.send_json({
                    "type": "response",
                    "id": uuid.uuid4().hex[:12],
                    "role": "assistant",
                    "message": result,
                    "topic": topic,
                    "elapsed_ms": round(elapsed, 1),
                    "routed_by": routed_by,
                })
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "error",
                    "error": f"Timeout: {topic} did not respond in 180s",
                })
            except RuntimeError as e:
                await websocket.send_json({
                    "type": "error",
                    "error": str(e),
                })

    except WebSocketDisconnect:
        pass


@app.on_event("shutdown")
async def shutdown():
    global _agent
    if _agent:
        await _agent.stop()
        _agent = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8402, log_level="info")
