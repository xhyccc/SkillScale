#!/usr/bin/env python3
"""Helper script for UI backend to publish ZMQ messages from inside Docker.

Usage:
    python3 docker_publish.py <topic> <timeout> <<< "message text"

Outputs a single JSON line to stdout with either the full response or an error.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skillscale.client import SkillScaleClient, ClientConfig


async def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: docker_publish.py <topic> <timeout>"}))
        sys.exit(1)

    topic = sys.argv[1]
    timeout = float(sys.argv[2])
    message = sys.stdin.read().strip()

    if not message:
        print(json.dumps({"error": "No message on stdin"}))
        sys.exit(1)

    config = ClientConfig(
        proxy_xsub=os.getenv("SKILLSCALE_PROXY_XSUB", "tcp://proxy:5444"),
        proxy_xpub=os.getenv("SKILLSCALE_PROXY_XPUB", "tcp://proxy:5555"),
        default_timeout=timeout,
    )
    client = SkillScaleClient(config)
    await client.connect()

    try:
        result = await client.invoke_raw(topic, message, timeout=timeout)
        print(json.dumps(result))
    except asyncio.TimeoutError:
        print(json.dumps({"error": "timeout"}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
