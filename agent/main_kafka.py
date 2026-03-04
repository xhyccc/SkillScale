"""
CLI Agent for Kafka.
"""
import asyncio
import logging
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from skillscale.kafka import SkillScaleKafkaClient

async def main():
    logging.basicConfig(level=logging.INFO)
    client = SkillScaleKafkaClient()
    await client.connect()

    print("Connected to Redpanda Broker.")
    
    # Simple loop
    while True:
        try:
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            if not line: break
            
            # Send to default topic for demo
            intent = line.strip()
            print(f"Sending intent: {intent}")
            
            # Assuming TOPIC_DATA_PROCESSING for test
            result = await client.invoke("TOPIC_DATA_PROCESSING", intent)
            print(f"Reply: {result}")
            
        except KeyboardInterrupt:
            break

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
