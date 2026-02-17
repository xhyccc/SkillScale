#!/usr/bin/env python3
"""
SkillScale — Python Skill Server with OpenSkills Invocation

A ZeroMQ-based skill server that:
  1. Reads AGENTS.md to discover installed skills (OpenSkills format).
  2. Subscribes to a topic on the ZMQ XPUB/XSUB proxy.
  3. Uses LLM-powered matching to select the best skill for each task.
  4. Loads the matched SKILL.md on demand (progressive disclosure).
  5. Executes scripts/run.py and returns the result.

Speaks the same ZMQ protocol as the C++ skill server, so it is a
drop-in replacement that works with the existing proxy and agent.

Usage:
    python server.py --topic TOPIC_DATA_PROCESSING \
                     --skills-dir ./skills/data-processing \
                     --proxy-xpub tcp://127.0.0.1:5555 \
                     --proxy-xsub tcp://127.0.0.1:5444
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
import threading
from pathlib import Path

import zmq

# ── Local imports ──────────────────────────────────────────
# Add parent dir so skills/llm_utils.py and skill-server-py/openskills.py
# are importable.
sys.path.insert(0, os.path.dirname(__file__))
from openskills import (
    SkillEntry,
    parse_agents_md,
    read_skill_md,
    execute_skill,
)

# Add project root for llm_utils
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "skills"))

logging.basicConfig(
    level=logging.INFO,
    format="[skill-server] %(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("skill-server-py")

# ──────────────────────────────────────────────────────────
#  Graceful shutdown
# ──────────────────────────────────────────────────────────
_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Signal %d received, shutting down...", sig)
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ──────────────────────────────────────────────────────────
#  LLM-powered skill matcher
# ──────────────────────────────────────────────────────────

MATCHER_SYSTEM_PROMPT = """\
You are a skill matcher for a skill server. You are given:
1. A list of available skills with descriptions.
2. A user task description.

Your job is to decide which SKILL best matches the task.

Rules:
- Reply with ONLY the skill name (e.g., text-summarizer).
- Do NOT include any explanation, punctuation, or extra text.
- Choose the single best-matching skill based on the task.
"""


def match_skill_llm(
    task_text: str,
    skills: list[SkillEntry],
) -> str | None:
    """
    Use the LLM to match a task description to the best available skill.
    Returns the skill name, or None if no match.
    """
    if not skills:
        return None

    # Single skill — no need for LLM
    if len(skills) == 1:
        log.info("[matcher] Single skill available: %s", skills[0].name)
        return skills[0].name

    try:
        from llm_utils import chat
    except ImportError:
        log.warning("[matcher] llm_utils not available, falling back to first skill")
        return skills[0].name

    skill_list = "\n".join(
        f"  - {s.name}: {s.description}" for s in skills
    )
    user_msg = (
        f"Available skills:\n{skill_list}\n\n"
        f"Valid skill names: {', '.join(s.name for s in skills)}\n\n"
        f"User task: {task_text}\n\n"
        f"Which skill should handle this task? Reply with ONLY the skill name."
    )

    try:
        reply = chat(
            MATCHER_SYSTEM_PROMPT,
            user_msg,
            max_tokens=50,
            temperature=0.0,
        ).strip()
    except Exception as e:
        log.error("[matcher] LLM call failed: %s — falling back to first skill", e)
        return skills[0].name

    # Sanitize: extract the skill name from the reply
    for s in skills:
        if s.name in reply:
            log.info("[matcher] LLM matched: %s", s.name)
            return s.name

    # Exact match fallback
    reply_clean = reply.strip().strip("`\"'").lower()
    for s in skills:
        if s.name.lower() == reply_clean:
            log.info("[matcher] LLM matched (cleaned): %s", s.name)
            return s.name

    log.warning("[matcher] LLM reply '%s' didn't match any skill, using first", reply)
    return skills[0].name


# ──────────────────────────────────────────────────────────
#  Request / Response protocol (same as C++ skill server)
# ──────────────────────────────────────────────────────────

def parse_request(payload: str) -> dict | None:
    """Parse incoming JSON request. Returns dict or None on error."""
    try:
        j = json.loads(payload)
        if "request_id" not in j or "reply_to" not in j or "intent" not in j:
            log.error("Missing required fields in request")
            return None
        return j
    except json.JSONDecodeError as e:
        log.error("JSON parse error: %s", e)
        return None


def make_response(request_id: str, status: str, content: str = "",
                  error: str = "") -> str:
    """Build the JSON response payload."""
    return json.dumps({
        "request_id": request_id,
        "status": status,
        "content": content,
        "error": error,
        "timestamp": time.time(),
    })


# ──────────────────────────────────────────────────────────
#  Worker thread — processes tasks from inproc PUSH/PULL
# ──────────────────────────────────────────────────────────

def worker_thread(
    ctx: zmq.Context,
    skills_dir: str,
    skills: list[SkillEntry],
    proxy_xsub: str,
    timeout_sec: int,
):
    """Worker that receives tasks, matches skills, and publishes results."""
    # PUB → proxy XSUB (to send responses)
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.SNDHWM, 10000)
    pub.setsockopt(zmq.LINGER, 1000)
    pub.connect(proxy_xsub)

    # PULL from inproc dispatcher
    pull = ctx.socket(zmq.PULL)
    pull.connect("inproc://workers")

    while _running:
        if not pull.poll(500):
            continue

        try:
            topic_bytes = pull.recv(zmq.NOBLOCK)
            payload_bytes = pull.recv(zmq.NOBLOCK)
        except zmq.Again:
            continue

        topic_str = topic_bytes.decode("utf-8", errors="replace")
        payload_str = payload_bytes.decode("utf-8", errors="replace")

        req = parse_request(payload_str)
        if not req:
            continue

        request_id = req["request_id"]
        reply_to = req["reply_to"]
        intent = req["intent"]

        log.info("[worker] Processing %s: %s...", request_id, intent[:80])

        # ── Parse intent: Mode 1 (explicit) or Mode 2 (task-based) ──
        skill_name = None
        exec_input = intent

        try:
            intent_json = json.loads(intent)
            # Mode 1: explicit skill name
            if "skill" in intent_json:
                skill_name = intent_json["skill"]
            # Extract data/task
            if "data" in intent_json:
                exec_input = intent_json["data"]
            elif "task" in intent_json:
                exec_input = intent_json["task"]
            # Mode 2: LLM match by task description
            if not skill_name and "task" in intent_json:
                task_text = intent_json["task"]
                skill_name = match_skill_llm(task_text, skills)
        except (json.JSONDecodeError, TypeError):
            # Plain text intent — Mode 2
            log.info("[worker] Plain text intent, matching by LLM")
            skill_name = match_skill_llm(intent, skills)

        if not skill_name:
            resp = make_response(request_id, "error",
                                 error="No matching skill found")
            pub.send_multipart([reply_to.encode(), resp.encode()])
            continue

        # ── Find skill entry ──
        entry = next((s for s in skills if s.name == skill_name), None)
        if not entry:
            resp = make_response(request_id, "error",
                                 error=f"Skill '{skill_name}' not found")
            pub.send_multipart([reply_to.encode(), resp.encode()])
            continue

        # ── Progressive disclosure: load SKILL.md on demand ──
        detail = read_skill_md(skills_dir, entry)
        if not detail:
            resp = make_response(request_id, "error",
                                 error=f"Could not load SKILL.md for '{skill_name}'")
            pub.send_multipart([reply_to.encode(), resp.encode()])
            continue

        log.info("[worker] Invoking skill: %s (via OpenSkills)", detail.name)

        # ── Execute skill ──
        result = execute_skill(detail, exec_input, timeout_sec=timeout_sec)

        if result.success:
            resp = make_response(request_id, "success", content=result.stdout)
        else:
            resp = make_response(
                request_id, "error",
                error=f"Skill execution failed (exit={result.exit_code}): "
                      f"{result.stderr}",
            )

        pub.send_multipart([reply_to.encode(), resp.encode()])
        log.info("[worker] Published response on %s", reply_to)

    pub.close()
    pull.close()


# ──────────────────────────────────────────────────────────
#  Main — subscribe to topic, dispatch to workers
# ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SkillScale Python Skill Server (OpenSkills)")
    parser.add_argument("--topic", default=os.getenv("SKILLSCALE_TOPIC", "TOPIC_DEFAULT"))
    parser.add_argument("--description", default=os.getenv("SKILLSCALE_DESCRIPTION", ""))
    parser.add_argument("--skills-dir", default=os.getenv("SKILLSCALE_SKILLS_DIR", "./skills"))
    parser.add_argument("--proxy-xpub", default=os.getenv("SKILLSCALE_PROXY_XPUB", "tcp://127.0.0.1:5555"))
    parser.add_argument("--proxy-xsub", default=os.getenv("SKILLSCALE_PROXY_XSUB", "tcp://127.0.0.1:5444"))
    parser.add_argument("--workers", type=int, default=int(os.getenv("SKILLSCALE_WORKERS", "2")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("SKILLSCALE_TIMEOUT", "120")))
    args = parser.parse_args()

    skills_dir = os.path.abspath(args.skills_dir)
    log.info("SkillScale Python Skill Server starting")
    log.info("  Topic      : %s", args.topic)
    log.info("  Description: %s", args.description or "(none)")
    log.info("  Skills dir : %s", skills_dir)
    log.info("  Proxy XPUB : %s", args.proxy_xpub)
    log.info("  Proxy XSUB : %s", args.proxy_xsub)
    log.info("  Workers    : %s", args.workers)
    log.info("  Timeout    : %ss", args.timeout)

    # ── Load AGENTS.md (OpenSkills discovery) ──
    agents_md_path = os.path.join(skills_dir, "AGENTS.md")
    skills = parse_agents_md(agents_md_path)

    if not skills:
        log.warning("No skills found in %s — server will not match any tasks",
                    agents_md_path)

    # Print skill metadata
    metadata = {
        "topic": args.topic,
        "description": args.description,
        "intent_modes": ["explicit", "task-based"],
        "skills": [{"name": s.name, "description": s.description} for s in skills],
    }
    log.info("Skill metadata: %s", json.dumps(metadata, indent=2))

    # ── ZeroMQ setup ──
    ctx = zmq.Context(2)

    # SUB ← proxy XPUB (receive intents)
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVHWM, 10000)
    sub.setsockopt(zmq.TCP_KEEPALIVE, 1)
    sub.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 60)
    sub.setsockopt(zmq.HEARTBEAT_IVL, 5000)
    sub.setsockopt(zmq.HEARTBEAT_TTL, 15000)
    sub.setsockopt(zmq.HEARTBEAT_TIMEOUT, 15000)
    sub.setsockopt(zmq.RECONNECT_IVL, 100)
    sub.setsockopt(zmq.RECONNECT_IVL_MAX, 5000)
    sub.connect(args.proxy_xpub)
    sub.setsockopt_string(zmq.SUBSCRIBE, args.topic)
    log.info("Subscribed to: %s", args.topic)

    # PUSH → inproc workers
    push = ctx.socket(zmq.PUSH)
    push.bind("inproc://workers")

    # Wait for subscription propagation
    log.info("Waiting for subscription propagation...")
    time.sleep(0.5)

    # ── Spawn worker threads ──
    threads = []
    for i in range(args.workers):
        t = threading.Thread(
            target=worker_thread,
            args=(ctx, skills_dir, skills, args.proxy_xsub, args.timeout),
            daemon=True,
        )
        t.start()
        threads.append(t)

    log.info("Ready. Listening for intents on %s", args.topic)

    # ── Main loop: SUB → PUSH dispatcher ──
    while _running:
        if not sub.poll(250):
            continue

        try:
            topic_msg = sub.recv(zmq.NOBLOCK)
            payload_msg = sub.recv(zmq.NOBLOCK)
        except zmq.Again:
            continue

        push.send(topic_msg, zmq.SNDMORE)
        push.send(payload_msg)

    log.info("Shutting down...")

    # Clean up
    sub.close()
    push.close()
    for t in threads:
        t.join(timeout=2)
    ctx.term()
    log.info("Shutdown complete.")


if __name__ == "__main__":
    main()
