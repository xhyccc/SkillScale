"""
SkillScale ↔ LangGraph Adapter

Provides pre-built LangGraph nodes and a graph factory that wires
SkillScale skill invocation into a LangGraph StateGraph. Each skill
becomes a node; the LLM router decides which node to invoke.

Usage:
    from skillscale import SkillScaleClient
    from skillscale.adapters.langgraph import SkillScaleGraph

    client = SkillScaleClient()
    await client.connect()

    sg = SkillScaleGraph.from_skills_dir(client, "./skills")
    graph = sg.build_graph(llm)
    result = await graph.ainvoke({"input": "summarize this text..."})
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, TypedDict

from skillscale.client import SkillScaleClient
from skillscale.discovery import SkillDiscovery, SkillMetadata

log = logging.getLogger("skillscale.langgraph")

try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False


def _check_langgraph():
    if not HAS_LANGGRAPH:
        raise ImportError(
            "LangGraph is required for this adapter. "
            "Install with: pip install langgraph"
        )


# ──────────────────────────────────────────────────────────
#  Graph State schema
# ──────────────────────────────────────────────────────────
class SkillScaleState(TypedDict, total=False):
    """State flowing through the LangGraph skill graph."""
    input: str              # original user query
    selected_skill: str     # skill name chosen by the router
    intent_payload: str     # formatted intent for the skill server
    intent_mode: str        # "explicit" or "task-based"
    skill_result: str       # raw markdown from the skill server
    output: str             # final response to user
    error: str              # error message if any


# ──────────────────────────────────────────────────────────
#  Node functions (stateless, composable)
# ──────────────────────────────────────────────────────────
def make_router_node(
    discovery: SkillDiscovery,
    llm: Any = None,
    task_based: bool = False,
):
    """
    Create a router node that picks the best skill for the user's input.

    If ``task_based=True`` (Mode 2), the node sends the raw task
    description to the skill server and lets it match locally — no
    client-side skill selection needed.

    If an LLM is provided and ``task_based=False``, it uses the LLM to
    classify the intent. Otherwise, falls back to simple keyword matching.
    """

    async def router(state: SkillScaleState) -> SkillScaleState:
        user_input = state["input"]
        skills = discovery.list_skills()

        # Mode 2: task-based — let the C++ server do the matching
        if task_based:
            # Route to the first available topic (or pick by LLM if desired)
            topics = discovery.list_topics()
            topic = topics[0] if topics else "TOPIC_DEFAULT"
            state["selected_skill"] = ""
            state["intent_mode"] = "task-based"
            state["intent_payload"] = json.dumps({"task": user_input})
            # Store the topic for the skill node
            state["_topic"] = topic
            return state

        # Mode 1: explicit — select a specific skill
        if llm is not None:
            # Use LLM to pick the skill
            skill_names = [s.name for s in skills]
            prompt = (
                f"Given the following user request, choose the most appropriate "
                f"skill from this list: {skill_names}\n\n"
                f"User request: {user_input}\n\n"
                f"Respond with ONLY the skill name, nothing else."
            )
            try:
                if asyncio.iscoroutinefunction(getattr(llm, 'ainvoke', None)):
                    response = await llm.ainvoke(prompt)
                else:
                    response = llm.invoke(prompt)
                chosen = str(getattr(response, 'content', response)).strip()
                # Validate chosen skill exists
                if chosen not in skill_names:
                    chosen = skill_names[0] if skill_names else ""
            except Exception as e:
                log.error("LLM router failed: %s", e)
                chosen = skill_names[0] if skill_names else ""
        else:
            # Simple keyword matching fallback
            chosen = _keyword_match(user_input, skills)

        state["selected_skill"] = chosen
        state["intent_mode"] = "explicit"
        state["intent_payload"] = json.dumps({
            "skill": chosen,
            "data": user_input,
        })
        return state

    return router


def _keyword_match(text: str, skills: List[SkillMetadata]) -> str:
    """Naive keyword scorer — picks the skill whose description best matches."""
    text_lower = text.lower()
    best_skill = skills[0].name if skills else ""
    best_score = 0

    for skill in skills:
        desc_words = set(skill.description.lower().split())
        name_words = set(skill.name.replace("-", " ").split())
        all_keywords = desc_words | name_words
        score = sum(1 for w in all_keywords if w in text_lower)
        if score > best_score:
            best_score = score
            best_skill = skill.name
    return best_skill


def make_skill_node(client: SkillScaleClient, discovery: SkillDiscovery):
    """
    Create a skill-execution node that invokes the selected skill
    through the SkillScale ZMQ middleware.
    """

    async def invoke_skill(state: SkillScaleState) -> SkillScaleState:
        skill_name = state.get("selected_skill", "")
        intent = state.get("intent_payload", state.get("input", ""))
        mode = state.get("intent_mode", "explicit")

        # In task-based mode, we don't need to resolve skill metadata
        # — the C++ server does the matching
        if mode == "task-based":
            topic = state.get("_topic", "")
            if not topic:
                topics = discovery.list_topics()
                topic = topics[0] if topics else "TOPIC_DEFAULT"
            try:
                result = await client.invoke(topic, intent)
                state["skill_result"] = result
                state["error"] = ""
            except asyncio.TimeoutError:
                state["error"] = f"Timeout invoking topic '{topic}' (task-based)"
                state["skill_result"] = ""
            except RuntimeError as e:
                state["error"] = str(e)
                state["skill_result"] = ""
            return state

        # Mode 1: explicit skill invocation
        skill_meta = discovery.get_skill(skill_name)
        if not skill_meta:
            state["error"] = f"Unknown skill: {skill_name}"
            state["skill_result"] = ""
            return state

        try:
            result = await client.invoke(skill_meta.topic, intent)
            state["skill_result"] = result
            state["error"] = ""
        except asyncio.TimeoutError:
            state["error"] = f"Timeout invoking skill '{skill_name}'"
            state["skill_result"] = ""
        except RuntimeError as e:
            state["error"] = str(e)
            state["skill_result"] = ""

        return state

    return invoke_skill


def make_output_node():
    """Create the final output node that formats the response."""

    async def format_output(state: SkillScaleState) -> SkillScaleState:
        if state.get("error"):
            state["output"] = f"**Error:** {state['error']}"
        else:
            state["output"] = state.get("skill_result", "No result.")
        return state

    return format_output


# ──────────────────────────────────────────────────────────
#  Graph builder
# ──────────────────────────────────────────────────────────
class SkillScaleGraph:
    """
    Factory that assembles a LangGraph StateGraph wired to the
    SkillScale middleware.

    Nodes:
      router → invoke_skill → format_output → END
    """

    def __init__(self, client: SkillScaleClient, discovery: SkillDiscovery):
        _check_langgraph()
        self.client = client
        self.discovery = discovery

    @classmethod
    def from_skills_dir(
        cls, client: SkillScaleClient, skills_dir: str
    ) -> "SkillScaleGraph":
        disc = SkillDiscovery(skills_root=skills_dir).scan()
        return cls(client, disc)

    def build_graph(self, llm: Any = None, task_based: bool = False):
        """
        Construct and compile a LangGraph workflow.

        Args:
            llm: optional LangChain-compatible LLM for intelligent routing.
                 If None, uses keyword-based skill matching.
            task_based: if True, uses Mode 2 (task-based) intent where
                        the C++ skill server matches skills by description
                        instead of the client selecting a skill explicitly.

        Returns:
            A compiled LangGraph that can be invoked with:
                result = await graph.ainvoke({"input": "user query"})
        """
        graph = StateGraph(SkillScaleState)

        graph.add_node("router", make_router_node(self.discovery, llm, task_based))
        graph.add_node("invoke_skill", make_skill_node(self.client, self.discovery))
        graph.add_node("format_output", make_output_node())

        graph.set_entry_point("router")
        graph.add_edge("router", "invoke_skill")
        graph.add_edge("invoke_skill", "format_output")
        graph.add_edge("format_output", END)

        return graph.compile()

    def get_available_skills(self) -> List[SkillMetadata]:
        return self.discovery.list_skills()

    def get_metadata_prompt(self) -> str:
        return self.discovery.metadata_summary()
