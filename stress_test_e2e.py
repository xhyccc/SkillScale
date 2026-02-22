#!/usr/bin/env python3
"""Concurrent stress test suite for SkillScale end-to-end latency.

Supports two modes:
  1) chat-api    -> POST /api/chat (full UI/API -> ZMQ -> skill server path)
  2) docker-exec -> docker compose exec agent/docker_publish.py (direct ZMQ path)

Example:
  source .venv/bin/activate
  python3 stress_test_e2e.py --mode chat-api --requests 200 --concurrency 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class RequestResult:
    index: int
    ok: bool
    latency_ms: float
    status_code: int
    error: str = ""
    topic: str = ""


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (p / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def build_message(index: int, base_message: str) -> str:
    return f"{base_message}\n\n[stress_request_id={index}]"


async def call_chat_api(
    *,
    index: int,
    url: str,
    base_message: str,
    timeout_s: float,
    topic: Optional[str],
) -> RequestResult:
    payload: dict[str, Any] = {
        "message": build_message(index, base_message),
        "timeout": timeout_s,
    }
    if topic:
        payload["topic"] = topic

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _post() -> tuple[int, Any]:
        with urllib.request.urlopen(request, timeout=timeout_s + 2) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return int(resp.status), parsed

    start = time.perf_counter()
    try:
        status, data = await asyncio.to_thread(_post)
        latency_ms = (time.perf_counter() - start) * 1000
        if status == 200 and isinstance(data, dict) and data.get("message"):
            return RequestResult(
                index=index,
                ok=True,
                latency_ms=latency_ms,
                status_code=status,
                topic=str(data.get("topic", "")),
            )
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("detail") or data.get("error") or data)
        else:
            detail = str(data)
        return RequestResult(
            index=index,
            ok=False,
            latency_ms=latency_ms,
            status_code=status,
            error=detail,
        )
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body[:500]
        except Exception:
            detail = str(exc)
        return RequestResult(
            index=index,
            ok=False,
            latency_ms=latency_ms,
            status_code=exc.code,
            error=detail,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(
            index=index,
            ok=False,
            latency_ms=latency_ms,
            status_code=0,
            error=str(exc),
        )


async def call_docker_exec(
    *,
    index: int,
    topic: str,
    timeout_s: float,
    base_message: str,
) -> RequestResult:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "agent",
        "python3",
        "agent/docker_publish.py",
        topic,
        str(timeout_s),
    ]

    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(build_message(index, base_message).encode("utf-8"))
        latency_ms = (time.perf_counter() - start) * 1000

        if proc.returncode != 0:
            return RequestResult(
                index=index,
                ok=False,
                latency_ms=latency_ms,
                status_code=proc.returncode,
                error=err.decode("utf-8", errors="replace")[:500],
            )

        stdout = out.decode("utf-8", errors="replace")
        payload: dict[str, Any] = {}
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                payload = json.loads(line)
                break

        if not payload:
            return RequestResult(
                index=index,
                ok=False,
                latency_ms=latency_ms,
                status_code=0,
                error=f"No JSON found in stdout: {stdout[:300]}",
            )

        if payload.get("status") == "success":
            return RequestResult(
                index=index,
                ok=True,
                latency_ms=latency_ms,
                status_code=0,
                topic=topic,
            )

        return RequestResult(
            index=index,
            ok=False,
            latency_ms=latency_ms,
            status_code=0,
            error=str(payload.get("error") or payload),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(
            index=index,
            ok=False,
            latency_ms=latency_ms,
            status_code=0,
            error=str(exc),
        )


async def run_stress(args: argparse.Namespace) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(args.concurrency)
    total = args.requests

    async def run_one(index: int) -> RequestResult:
        async with semaphore:
            if args.mode == "chat-api":
                return await call_chat_api(
                    index=index,
                    url=args.url,
                    base_message=args.message,
                    timeout_s=args.timeout,
                    topic=args.topic,
                )

            return await call_docker_exec(
                index=index,
                topic=args.topic,
                timeout_s=args.timeout,
                base_message=args.message,
            )

    if args.warmup > 0:
        print(f"Warmup: sending {args.warmup} request(s)...")
        for i in range(args.warmup):
            _ = await run_one(-(i + 1))

    print(
        f"Running stress test: mode={args.mode}, requests={args.requests}, "
        f"concurrency={args.concurrency}, timeout={args.timeout}s"
    )

    start_wall = time.perf_counter()

    results: list[RequestResult]
    tasks = [asyncio.create_task(run_one(i + 1)) for i in range(total)]
    results = await asyncio.gather(*tasks)

    duration_s = time.perf_counter() - start_wall

    ok_results = [r for r in results if r.ok]
    fail_results = [r for r in results if not r.ok]
    latencies = [r.latency_ms for r in ok_results]

    summary: dict[str, Any] = {
        "mode": args.mode,
        "url": args.url if args.mode == "chat-api" else "",
        "topic": args.topic,
        "requests": total,
        "concurrency": args.concurrency,
        "timeout_s": args.timeout,
        "duration_s": round(duration_s, 3),
        "throughput_rps": round((total / duration_s) if duration_s > 0 else 0.0, 3),
        "success": len(ok_results),
        "failures": len(fail_results),
        "success_rate": round((len(ok_results) / total) * 100.0 if total else 0.0, 2),
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0.0,
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 50), 2) if latencies else 0.0,
            "p90": round(percentile(latencies, 90), 2) if latencies else 0.0,
            "p95": round(percentile(latencies, 95), 2) if latencies else 0.0,
            "p99": round(percentile(latencies, 99), 2) if latencies else 0.0,
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "sample_failures": [
            {
                "index": r.index,
                "status_code": r.status_code,
                "error": r.error[:200],
                "latency_ms": round(r.latency_ms, 2),
            }
            for r in fail_results[:10]
        ],
    }

    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Concurrent stress test suite for SkillScale end-to-end latency"
    )
    parser.add_argument(
        "--mode",
        choices=["chat-api", "docker-exec"],
        default="chat-api",
        help="Test path: chat-api (default) or docker-exec",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8401/api/chat",
        help="Chat API URL (used only with --mode chat-api)",
    )
    parser.add_argument(
        "--topic",
        default="TOPIC_DATA_PROCESSING",
        help="Topic to test. For chat-api this is sent as manual topic; for docker-exec it is required.",
    )
    parser.add_argument(
        "--message",
        default="summarize this paragraph: SkillScale provides distributed skill execution over ZeroMQ.",
        help="Base request message",
    )
    parser.add_argument("--requests", type=int, default=100, help="Total number of requests")
    parser.add_argument(
        "--concurrency", type=int, default=10, help="Max in-flight concurrent requests"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Warmup requests before measurement",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to write machine-readable JSON summary",
    )

    args = parser.parse_args()
    if args.requests <= 0:
        parser.error("--requests must be > 0")
    if args.concurrency <= 0:
        parser.error("--concurrency must be > 0")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.warmup < 0:
        parser.error("--warmup must be >= 0")
    return args


def print_summary(summary: dict[str, Any]):
    latency = summary["latency_ms"]
    print("\n=== Stress Test Summary ===")
    print(f"mode            : {summary['mode']}")
    if summary["url"]:
        print(f"url             : {summary['url']}")
    print(f"topic           : {summary['topic']}")
    print(f"requests        : {summary['requests']}")
    print(f"concurrency     : {summary['concurrency']}")
    print(f"duration_s      : {summary['duration_s']}")
    print(f"throughput_rps  : {summary['throughput_rps']}")
    print(
        f"success/fail    : {summary['success']}/{summary['failures']} "
        f"({summary['success_rate']}%)"
    )
    print("latency_ms      : "
          f"min={latency['min']}, mean={latency['mean']}, "
          f"p50={latency['p50']}, p90={latency['p90']}, "
          f"p95={latency['p95']}, p99={latency['p99']}, max={latency['max']}")

    if summary["sample_failures"]:
        print("\nSample failures (up to 10):")
        for item in summary["sample_failures"]:
            print(
                f"  - request#{item['index']} status={item['status_code']} "
                f"latency_ms={item['latency_ms']} error={item['error']}"
            )


async def _async_main() -> int:
    args = parse_args()
    summary = await run_stress(args)
    print_summary(summary)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fp:
            json.dump(summary, fp, indent=2, ensure_ascii=False)
        print(f"\nWrote JSON summary to {args.json_out}")

    return 0 if summary["success"] > 0 else 1


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
