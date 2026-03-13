#!/usr/bin/env python3
"""
Google Agent-to-Agent (A2A) Client Demo

Coarse-grained: the caller only specifies *which agent* to talk to via the URL path.
The Gateway derives the Kafka topic automatically (code-analysis → TOPIC_CODE_ANALYSIS)
and the Skill Server uses AGENTS.md to decide which skill to run.

    POST /v1/agents/{agent_id}/converse
    Body: A2A TaskSendParams (message only, no routing metadata needed)
"""

import os
import sys
import urllib.request
import json
import uuid

from a2a_protocol.pydantic_v2 import (
    TaskSendParams,
    Message,
    Role,
    Part,
    TextPart,
)

GATEWAY_URL = "http://127.0.0.1:8085"


def main():
    agent_id = "code-analysis"
    url = f"{GATEWAY_URL}/v1/agents/{agent_id}/converse"

    code_text = (
        "def process_data(data):\n"
        "    count = 0\n"
        "    for item in data:\n"
        "        if item > 10:\n"
        "            count += 1\n"
        "            if count > 5:\n"
        "                return True\n"
        "    return False\n"
    )

    # A2A: only agent_id in the URL, no topic/skill metadata needed
    params = TaskSendParams(
        id=f"task_{uuid.uuid4().hex[:8]}",
        sessionId=f"session_{uuid.uuid4().hex[:8]}",
        message=Message(
            role=Role.user,
            parts=[Part(root=TextPart(type="text", text=code_text))],
        ),
    )

    payload = params.model_dump(mode="json", exclude_none=True)

    print("Starting A2A Client Demo...")
    print(f"  Agent:   {agent_id}")
    print(f"  Target:  {url}")
    print(f"  Payload: {json.dumps(payload, indent=2)}")
    print("-" * 50)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        timeout = int(float(os.environ.get('SKILLSCALE_GATEWAY_TIMEOUT', '600')))
        with urllib.request.urlopen(req, timeout=timeout) as response:
            resp_data = json.loads(response.read().decode("utf-8"))
            print("\n[A2A] Response:")
            print(json.dumps(resp_data, indent=2))
    except Exception as e:
        print(f"A2A request failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
