
"""
Standalone Kafka Skill Server wrapper (replaces C++).
"""
import asyncio
import logging
import argparse
import sys
import os
import signal

# Ensure we can import skillscale from root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add parent directory to sys.path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from skillscale.kafka import SkillScaleKafkaServer
except ImportError:
    # If explicit import fails (e.g. if not installed as package), try direct import
    from kafka import SkillScaleKafkaServer

# Simple mock handler for now to prove connectivity
async def execute_skill(intent: str) -> str:
    try:
        import json
        import subprocess
        
        # Parse the intent JSON
        # SkillScale gateway sends: {"skill": "name", "data": {"input": "code"}, ...}
        # Be resilient if intent is already a dict or string
        if isinstance(intent, str):
            try:
                data = json.loads(intent)
            except json.JSONDecodeError:
                data = {"data": {"input": intent}} # Fallback for raw string
        else:
            data = intent
            
        skill_name = data.get("skill", "code-complexity") # Default to code-complexity for demo
        
        # Locate the skill
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Paths to search for skills
        search_paths = [
            os.path.join(repo_root, ".claude", "skills"),
            os.path.join(repo_root, "skills", ".claude", "skills"),
            os.path.join(repo_root, "skills", "code-analysis", ".claude", "skills")
        ]
        
        skill_dir = None
        for base in search_paths:
            candidate = os.path.join(base, skill_name)
            if os.path.exists(candidate):
                skill_dir = candidate
                break
        
        if not skill_dir:
             return f"Error: Skill '{skill_name}' not found in {search_paths}"
             
        # Locate the run script
        run_script = os.path.join(skill_dir, "scripts", "run.py")
        if not os.path.exists(run_script):
            return f"Error: run.py not found at {run_script}"
            
        # Extract input code
        input_payload = data.get("data", {}).get("input", "")
        # If input is empty, maybe it's in data directly?
        if not input_payload and isinstance(data.get("data"), str):
            input_payload = data.get("data")
            
        if not input_payload:
             # Try to invoke with empty string if desired, or error
             pass 

        # Set environment for the skill execution
        env = os.environ.copy()
        env["SKILLSCALE_INTENT"] = input_payload
        # Add repo_root and repo_root/skills to PYTHONPATH
        skills_lib = os.path.join(repo_root, "skills")
        env["PYTHONPATH"] = f"{repo_root}:{skills_lib}:{env.get('PYTHONPATH', '')}"
        
        # Execute the skill
        cmd = [sys.executable, run_script]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        
        stdout, stderr = await process.communicate(input=input_payload.encode())
        
        if process.returncode != 0:
            return f"Error executing skill {skill_name}: {stderr.decode()}"
            
        return stdout.decode()
        
    except Exception as e:
        logging.exception("Error executing skill")
        return f"Error: {e}"

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True, help="Kafka topic to consume (e.g. SKILL_TOPIC_CODE_ANALYSIS)")
    parser.add_argument("--skills-dir", help="Path to skills directory (unused in mock)")
    args = parser.parse_args()

    # Instantiate server
    server = SkillScaleKafkaServer(args.topic, execute_skill)
    
    # Start server (this spawns background tasks)
    await server.start()
    
    # Create a future that will block until a signal is received
    stop_event = asyncio.Event()

    def signal_handler():
        logging.info("Signal received, shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    logging.info(f"Server is running on topic {args.topic}. Press Ctrl+C to stop.")
    
    # Keep the process alive
    await stop_event.wait()
    
    logging.info("Server stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

