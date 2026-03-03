import sys

filepath = 'gateway/transparent_layer.py'
with open(filepath, 'r') as f:
    content = f.read()

old_logic = """            # Map specific agent IDs to ZMQ Topics
            target_topic = "TOPIC_CODE_ANALYSIS"
            
            # Extract code text explicitly for our naive skill 
            # In a real implementation this would parse the multi-part data robustly
            extracted_code = ""
            for part in a2a_req.params.message.parts:
                if isinstance(part.root, TextPart):
                    extracted_code += part.root.text
            
            # Map Google A2A protocol structure back into our ZeroMQ bus intent model
            zmq_payload = {
                "skill": agent_id,
                "data": {"code": extracted_code},"""

new_logic = """            # Transparent middleware should be completely agnostic.
            # Extract the raw intent from A2A message parts generically.
            extracted_text = ""
            for part in a2a_req.params.message.parts:
                if isinstance(part.root, TextPart):
                    extracted_text += part.root.text + "\\n"
                    
            # Extract target topic from A2A metadata (standard mechanism for routing hints)
            target_topic = "TOPIC_DEFAULT"
            if a2a_req.params.metadata and "topic" in a2a_req.params.metadata:
                target_topic = a2a_req.params.metadata["topic"]
            
            # Map Google A2A protocol structure back into our ZeroMQ bus intent model
            zmq_payload = {
                "skill": agent_id,
                "data": {"input": extracted_text.strip()},"""

content = content.replace(old_logic, new_logic)

with open(filepath, 'w') as f:
    f.write(content)
