"""
SkillScale — Kafka/Redpanda Client & Server implementation.

Provides generic async Kafka client/server classes using aiokafka.
This parallels the ZMQ implementation in client.py but for distributed brokers.
"""
import asyncio
import json
import logging
import uuid
import os
from typing import Optional, Dict, Any, Callable

try:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
except ImportError:
    # Fallback to avoid breaking if aiokafka is not installed (e.g. in ZMQ env)
    AIOKafkaProducer = None
    AIOKafkaConsumer = None

log = logging.getLogger("skillscale.kafka")

class KafkaConfig:
    def __init__(self):
        self.bootstrap_servers = os.getenv("SKILLSCALE_BROKER_URL", "localhost:9092")
        self.client_id = f"client-{uuid.uuid4().hex[:8]}"
        self.group_id = os.getenv("SKILLSCALE_GROUP_ID", f"group-{uuid.uuid4().hex[:8]}")

class SkillScaleKafkaClient:
    """
    Async Kafka Client for producing intents and consuming replies.
    """
    def __init__(self, config: Optional[KafkaConfig] = None):
        self.config = config or KafkaConfig()
        if not AIOKafkaProducer:
            raise ImportError("aiokafka not installed. Install with `pip install aiokafka`")
        
        self.producer = None # Lazy initialization
        self.consumer = None
        self.reply_topic = f"reply-{self.config.client_id}"
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.running = False

    async def connect(self):
        if self.producer is None:
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.config.bootstrap_servers,
                client_id=self.config.client_id
            )
        await self.producer.start()
        # Setup ephemeral reply consumer
        self.consumer = AIOKafkaConsumer(
            self.reply_topic,
            bootstrap_servers=self.config.bootstrap_servers,
            group_id=None,  # No group for unique reply channel
            auto_offset_reset="latest"
        )
        await self.consumer.start()
        self.running = True
        asyncio.create_task(self._consume_loop())
        log.info(f"Connected to Redpanda at {self.config.bootstrap_servers}, listening on {self.reply_topic}")

    async def close(self):
        self.running = False
        await self.producer.stop()
        if self.consumer:
            await self.consumer.stop()

    async def invoke(self, topic: str, intent: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Send an intent to a topic and await the correlated response.
        """
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[request_id] = future

        payload = {
            "intent": intent,
            "metadata": {
                "reply_to": self.reply_topic,
                "request_id": request_id,
                "timestamp": asyncio.get_running_loop().time()
            }
        }
        
        try:
            # Produce to the Skill Topic (e.g., TOPIC_DATA_PROCESSING)
            await self.producer.send_and_wait(
                topic, 
                json.dumps(payload).encode("utf-8")
            )
            log.info(f"Sent request {request_id} to {topic}")
            
            # Wait for response
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            del self.pending_requests[request_id]
            raise
        except Exception as e:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            raise e

    async def _consume_loop(self):
        """Listen for replies on specific reply topic"""
        try:
            async for msg in self.consumer:
                try:
                    data = json.loads(msg.value.decode("utf-8"))
                    req_id = data.get("metadata", {}).get("request_id")
                    
                    if req_id and req_id in self.pending_requests:
                        future = self.pending_requests.pop(req_id)
                        if not future.done():
                            future.set_result(data)
                except Exception as e:
                    log.error(f"Error processing reply: {e}")
        except Exception:
            pass

class SkillScaleKafkaServer:
    """
    Generic Kafka Skill Server.
    Consumes from a shared Topic (Group) and executes Skills.
    """
    def __init__(self, topic: str, handler_func: Callable[[str], str]):
        self.config = KafkaConfig()
        self.topic = topic
        self.handler = handler_func
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.config.bootstrap_servers
        )
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.config.bootstrap_servers,
            group_id=self.config.group_id,
            auto_offset_reset="earliest"
        )

    async def start(self):
        await self.producer.start()
        await self.consumer.start()
        log.info(f"Kafka Skill Server started on {self.topic} (Group: {self.config.group_id})")
        asyncio.create_task(self._server_loop())

    async def _server_loop(self):
        async for msg in self.consumer:
            try:
                payload = json.loads(msg.value.decode("utf-8"))
                intent = payload.get("intent")
                meta = payload.get("metadata", {})
                reply_to = meta.get("reply_to")
                req_id = meta.get("request_id")

                log.info(f"Received task {req_id}: {intent[:50]}...")

                # Execute Skill Logic
                result_str = await self.handler(intent)

                # Send Reply
                if reply_to:
                    response = {
                        "status": "success",
                        "result": result_str,
                        "metadata": {"request_id": req_id}
                    }
                    await self.producer.send_and_wait(
                        reply_to,
                        json.dumps(response).encode("utf-8")
                    )
                    log.info(f"Replied to {reply_to}")

            except Exception as e:
                log.error(f"Error handling task: {e}")

