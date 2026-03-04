#!/usr/bin/env python3
import subprocess
import json
import time
import sys
import re

def run_client():
    print("🚀 Triggering Skill Execution via Gateway...")
    result = subprocess.run(
        ["python3", "gateway/demo_a2a_client.py"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print("❌ Client failed")
        print(result.stderr)
        sys.exit(1)
    
    # Extract Task ID from output
    # Looking for: "id": "task_..."
    match = re.search(r'"id": "(task_[a-f0-9]+)"', result.stdout)
    if not match:
        print("❌ Could not find Task ID in client output")
        print(result.stdout)
        sys.exit(1)
        
    task_id = match.group(1)
    print(f"✅ Application accepted task: {task_id}")
    return task_id

def watch_logs(task_id):
    print(f"⏳ Waiting for Task {task_id} to complete...")
    start_time = time.time()
    container = "skillscale-rust-skill-server-code-1"
    
    while True:
        if time.time() - start_time > 120:
            print("❌ Timeout waiting for execution")
            sys.exit(1)
            
        # Fetch recent logs
        result = subprocess.run(
            ["docker", "logs", "--tail", "100", container],
            capture_output=True,
            text=True
        )
        logs = result.stdout + result.stderr
        
        # Check for completion markers associated with this task
        # The logs show: Executing skill... then Skill execution successful
        # We need to ensure it's OUR task. The log lines don't always print the Task ID on the success line
        # but the executions are sequential in this queue.
        
        # Robust check: Look for "Received message: ... task_id" AND a SUBSEQUENT "Skill execution successful"
        # For simplicity, we'll just look for the output appearing AFTER we started.
        
        if f'"id":"{task_id}"' in logs:
            # Task was received. Now check for success/failure
            if "Skill execution successful" in logs or "Execution failed" in logs:
                # Find the log segment
                lines = logs.splitlines()
                for i, line in enumerate(lines):
                    if f'"id":"{task_id}"' in line:
                        # Found our request. Look forward for result.
                        for j in range(i, len(lines)):
                            if "Skill execution successful" in lines[j]:
                                print("\n🎉 Execution Complete!")
                                print("-" * 40)
                                print("\n".join(lines[j:]))
                                return
                            if "Execution failed" in lines[j]:
                                print("\n❌ Execution Failed!")
                                print("\n".join(lines[j:]))
                                sys.exit(1)
        
        time.sleep(2)

if __name__ == "__main__":
    task_id = run_client()
    watch_logs(task_id)
