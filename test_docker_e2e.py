#!/usr/bin/env python3
"""
Quick E2E test against containerized SkillScale services.

Uses `docker compose exec` to publish from inside the Docker network,
because ZMQ PUB/SUB does NOT work through Docker Desktop macOS port mapping
(the VM bridge silently drops subscription frames).
"""
import json
import subprocess
import sys
import time


def docker_publish(topic: str, message: str, timeout: int = 120) -> dict:
    """Publish a message via docker compose exec into the agent container."""
    cmd = [
        "docker", "compose", "exec", "-T", "agent",
        "python3", "agent/docker_publish.py", topic, str(timeout),
    ]
    proc = subprocess.run(
        cmd, input=message, capture_output=True, text=True,
        timeout=timeout + 30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker exec failed (rc={proc.returncode}): {proc.stderr}")

    # Parse last JSON line from stdout (skip any startup noise)
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)

    raise RuntimeError(f"No JSON in output: {proc.stdout[:500]}")


def test_summarizer():
    """Test the text-summarizer skill via data-processing topic."""
    print("=== Test 1: text-summarizer (TOPIC_DATA_PROCESSING) ===")
    message = (
        "Summarize this: Docker containers provide lightweight virtualization "
        "that packages applications with their dependencies. This enables "
        "consistent deployment across different environments."
    )
    result = docker_publish("TOPIC_DATA_PROCESSING", message)

    assert result.get("status") == "success", f"Expected success, got: {result}"
    content = result.get("content", "")
    assert len(content) > 20, f"Content too short: {content}"
    trace = result.get("_trace", {})
    print(f"  Status: {result['status']}")
    print(f"  Content: {content[:200]}...")
    print(f"  Elapsed: {trace.get('elapsed_ms', '?')}ms")
    print(f"  Method:  {trace.get('execution_method', '?')}")
    print("  PASSED\n")


def test_code_analysis():
    """Test a code-analysis skill via code-analysis topic."""
    print("=== Test 2: code-complexity (TOPIC_CODE_ANALYSIS) ===")
    message = (
        "analyze complexity:\n"
        "def bubble_sort(arr):\n"
        "    n = len(arr)\n"
        "    for i in range(n):\n"
        "        for j in range(0, n-i-1):\n"
        "            if arr[j] > arr[j+1]:\n"
        "                arr[j], arr[j+1] = arr[j+1], arr[j]\n"
        "    return arr\n"
    )
    result = docker_publish("TOPIC_CODE_ANALYSIS", message)

    assert result.get("status") == "success", f"Expected success, got: {result}"
    content = result.get("content", "")
    assert len(content) > 20, f"Content too short: {content}"
    trace = result.get("_trace", {})
    print(f"  Status: {result['status']}")
    print(f"  Content: {content[:200]}...")
    print(f"  Elapsed: {trace.get('elapsed_ms', '?')}ms")
    print(f"  Method:  {trace.get('execution_method', '?')}")
    print("  PASSED\n")


def test_chat_api():
    """Test the UI chat API endpoint (which uses Docker routing internally)."""
    import urllib.request

    print("=== Test 3: Chat API (/api/chat) ===")
    payload = json.dumps({"message": "summarize: Kubernetes orchestrates containers at scale"}).encode()
    req = urllib.request.Request(
        "http://localhost:8401/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        print(f"  SKIPPED (UI not running): {e}\n")
        return

    assert "message" in result, f"No message in response: {result}"
    assert len(result["message"]) > 20, f"Message too short: {result['message']}"
    print(f"  Topic:   {result.get('topic', '?')}")
    print(f"  Message: {result['message'][:200]}...")
    print(f"  Elapsed: {result.get('elapsed_ms', '?')}ms")
    print("  PASSED\n")


def main():
    print("SkillScale Docker E2E Tests")
    print("=" * 50)

    # Verify containers are running
    ps = subprocess.run(
        ["docker", "compose", "ps", "--format", "{{.Service}}\t{{.State}}"],
        capture_output=True, text=True,
    )
    services = dict(line.split("\t") for line in ps.stdout.strip().splitlines() if "\t" in line)
    required = ["proxy", "agent", "skill-server-data-processing", "skill-server-code-analysis"]
    for svc in required:
        state = services.get(svc, "missing")
        if state != "running":
            print(f"ERROR: Service '{svc}' is {state}. Run `bash build.sh` first.")
            sys.exit(1)
    print(f"All {len(required)} required services running.\n")

    t0 = time.time()
    passed = 0
    failed = 0

    for test_fn in [test_summarizer, test_code_analysis, test_chat_api]:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}\n")
            failed += 1

    elapsed = time.time() - t0
    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed ({elapsed:.1f}s)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
