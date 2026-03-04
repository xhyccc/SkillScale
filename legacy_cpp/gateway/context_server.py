import asyncio
import json
import logging
import time
import zmq
import zmq.asyncio

from skillscale.client import ClientConfig

log = logging.getLogger("skillscale.context_server")

class ContextServer:
    """
    A standalone ZeroMQ worker that listens on TOPIC_CONTEXT_SYNC.
    It holds shared context (memory, whiteboard, state) for all agents 
    and MCP clients connected via the Transparent Layer.
    """
    def __init__(self, config: ClientConfig = None):
        self.config = config or ClientConfig.from_env()
        self.ctx = zmq.asyncio.Context()
        self.store = {}  # In-memory store: { session_id : { key: val } }

    async def start(self):
        log.info("Starting Context Server...")
        
        # Publisher to send replies back to clients
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.connect(self.config.proxy_xsub)
        
        # Subscriber to listen to TOPIC_CONTEXT_SYNC
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.connect(self.config.proxy_xpub)
        self.sub.setsockopt_string(zmq.SUBSCRIBE, "TOPIC_CONTEXT_SYNC")
        
        log.info("Context Server listening on TOPIC_CONTEXT_SYNC...")
        
        while True:
            try:
                frames = await self.sub.recv_multipart()
                if len(frames) != 2:
                    continue
                    
                topic, payload_bytes = frames
                
                try:
                    payload = json.loads(payload_bytes.decode("utf-8"))
                    request_id = payload.get("request_id")
                    reply_to = payload.get("reply_to")
                    # Intent is wrapped by invoke_raw as a string, parse it again
                    intent = json.loads(payload.get("intent", "{}"))
                except Exception as e:
                    log.error(f"Failed to parse incoming payload: {e}")
                    continue
                
                action = intent.get("action")
                session_id = intent.get("session_id", "default")
                
                context_state = self.store.setdefault(session_id, {"history": [], "shared_variables": {}})
                
                log.info(f"Received context sync req={request_id} action={action} session={session_id}")
                
                # Process action
                result_content = ""
                if action == "get_state":
                    result_content = json.dumps(context_state)
                elif action == "update_state":
                    updates = intent.get("updates", {})
                    context_state["shared_variables"].update(updates)
                    result_content = "OK"
                else:
                    result_content = f"Unknown action: {action}"

                # Reply to the client's dedicated channel
                response = {
                    "request_id": request_id,
                    "status": "success",
                    "content": result_content,
                    "timestamp": time.time()
                }
                
                await self.pub.send_multipart([
                    reply_to.encode("utf-8"),
                    json.dumps(response).encode("utf-8")
                ])
                
            except Exception as e:
                log.error(f"Error processing message: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = ContextServer()
    asyncio.run(server.start())
