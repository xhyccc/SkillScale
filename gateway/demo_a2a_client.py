#!/usr/bin/env python3
"""
Google Agent-to-Agent (A2A) Client Demo
This script demonstrates how an external system communicating via Google A2A standards
could talk to SkillScale skills through the Transparent Gateway.

Assuming standard protocol wrappers:
POST /v1/agents/{agent_id}/converse
{
    "sender_agent_id": "demo_google_agent_1",
    "conversation_id": "session_888",
    "topic": "TOPIC_CODE_ANALYSIS",
    "message": {
        "code": "def hello():\n    return 'world'"
    }
}
"""

import sys
import os
import urllib.request
import json
import uuid
from enum import Enum
from datetime import datetime

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)

from a2a_protocol.pydantic_v2 import (
    SendTaskRequest,
    TaskSendParams,
    Message,
    Role,
    Part,
    TextPart
)

def main():
    print("Starting A2A Client Demo using a2a_protocol...")
    url = "http://127.0.0.1:8081/v1/agents/code-complexity/converse"
    
    code_text = "def process_data(data):\n    count = 0\n    for item in data:\n        if item > 10:\n            count += 1\n            if count > 5:\n                return True\n    return False\n"
    
    # Construct A2A request using SDK models
    a2a_req = SendTaskRequest(
        jsonrpc="2.0",
        id=str(uuid.uuid4()),
        method="tasks/send",
        params=TaskSendParams(
            id=f"task_{uuid.uuid4().hex[:8]}",
            sessionId="session_888",
            message=Message(
                role=Role.user,
                parts=[Part(root=TextPart(type="text", text=code_text))]
            ),
            metadata={"topic": "TOPIC_CODE_ANALYSIS"} # <--- Explicitly provide routing hints via metadata instead of letting the gateway guess
        )
    )
    
    # model_dump(mode='json') handles enum serialization correctly natively in pydantic v2
    payload = a2a_req.model_dump(mode='json', exclude_none=True)
    
    print("\n[A2A Client] Sending A2A protocol request to Transparent Gateway...")
    print(f"Target: {url}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print("-" * 50)
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, 
        data=data, 
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            if response.status == 200:
                resp_data = json.loads(response.read().decode('utf-8'))
                print("\n[A2A Client] Received A2A format response:")
                print(json.dumps(resp_data, indent=2))
            else:
                print(f"Error {response.status}: {response.read().decode('utf-8')}")
    except Exception as e:
        print(f"Connection failed (is transparent_layer.py running?): {e}")

if __name__ == "__main__":
    main()
