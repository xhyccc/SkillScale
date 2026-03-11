import subprocess
import json
import time
import os
import sys

def main():
    server_env = os.environ.copy()
    server_env["SKILLSCALE_BROKER_URL"] = "localhost:9092"
    server_env["RUST_BACKTRACE"] = "1"
    server_path = os.path.abspath("skillscale-rs/target/release/gateway")
    
    print(f"Starting server: {server_path}")
    
    proc = subprocess.Popen(
        [server_path, "--mcp"],
        env=server_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0  # Unbuffered
    )

    # Helper to clean read
    def read_response():
        line = proc.stdout.readline()
        if not line:
            print("Server closed stdout")
            return None
        print(f"Server sent: {line.strip()}")
        return json.loads(line)

    try:
        # 1. Initialize
        print("Sending initialize...")
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "manual-debug", "version": "1.0"}
            }
        }
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()
        
        resp = read_response()
        if not resp:
            print("No response to initialize")
            stderr = proc.stderr.read()
            print(f"Stderr: {stderr}")
            return

        # 2. Initialized Notification
        print("Sending initialized...")
        notif = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        }
        proc.stdin.write(json.dumps(notif) + "\n")
        proc.stdin.flush()
        
        # 3. List Tools
        print("Sending tools/list...")
        list_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        proc.stdin.write(json.dumps(list_req) + "\n")
        proc.stdin.flush()
        
        resp = read_response()
        if resp:
            print(f"Tools response: {json.dumps(resp, indent=2)}")
        else:
             print("Server closed stdout after tools/list")
             stderr = proc.stderr.read()
             print(f"Stderr: {stderr}")

    except Exception as e:
        print(f"Exception: {e}")
    finally:
        proc.kill()

if __name__ == "__main__":
    main()
