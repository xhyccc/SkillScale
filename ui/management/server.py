#!/usr/bin/env python3
"""
SkillScale Unified UI — FastAPI Backend

Merges management + chat + tracing into one server on port 8401.
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Project paths ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
PROXY_BIN = PROJECT_ROOT / "proxy" / "build" / "skillscale_proxy"
SERVER_BIN = PROJECT_ROOT / "skill-server" / "build" / "skillscale_skill_server"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

app = FastAPI(title="SkillScale", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════
#  Tracing infrastructure
# ══════════════════════════════════════════════════════════
@dataclass
class TraceSpan:
    name: str
    phase: str          # routing | zmq | skill_server | skill_exec
    start_ms: float = 0
    end_ms: float = 0
    duration_ms: float = 0
    details: dict = field(default_factory=dict)

    def finish(self, extra: dict | None = None):
        self.end_ms = time.time() * 1000
        self.duration_ms = round(self.end_ms - self.start_ms, 2)
        if extra:
            self.details.update(extra)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "phase": self.phase,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_ms": self.duration_ms,
            "details": self.details,
        }


@dataclass
class ChatTrace:
    trace_id: str
    message: str
    topic: str = ""
    result: str = ""
    status: str = "pending"
    total_ms: float = 0
    created_at: float = field(default_factory=time.time)
    spans: list = field(default_factory=list)

    def add_span(self, span: TraceSpan):
        self.spans.append(span)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "message": self.message[:200],
            "topic": self.topic,
            "result": self.result[:500] if self.result else "",
            "status": self.status,
            "total_ms": self.total_ms,
            "created_at": self.created_at,
            "spans": [s.to_dict() for s in self.spans],
        }


_traces: list[ChatTrace] = []
MAX_TRACES = 100


# ══════════════════════════════════════════════════════════
#  Service management state
# ══════════════════════════════════════════════════════════
@dataclass
class ServiceInfo:
    name: str
    pid: Optional[int] = None
    process: Optional[subprocess.Popen] = None
    log_file: Optional[str] = None
    topic: Optional[str] = None
    skills_dir: Optional[str] = None
    matcher: str = "llm"
    status: str = "stopped"
    started_at: Optional[float] = None


services: dict[str, ServiceInfo] = {}


# ══════════════════════════════════════════════════════════
#  Docker container detection
# ══════════════════════════════════════════════════════════
def _detect_docker_mode() -> bool:
    """Check if SkillScale Docker containers are running."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            return False
        output = result.stdout.strip()
        if not output:
            return False
        # docker compose ps --format json returns one JSON object per line
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                info = json.loads(line)
                # If any skillscale container is running, we're in Docker mode
                svc_name = info.get("Service", info.get("Name", ""))
                state = info.get("State", "").lower()
                if ("proxy" in svc_name or "skill-server" in svc_name) and state == "running":
                    return True
            except json.JSONDecodeError:
                continue
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_docker_services() -> list[dict]:
    """Get status of all SkillScale Docker containers."""
    result_list = []
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            return []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue

            svc_name = info.get("Service", info.get("Name", ""))
            state = info.get("State", "").lower()
            health = info.get("Health", "").lower()

            # Map Docker state to our status
            if state == "running":
                status = "running"
            elif state in ("exited", "dead"):
                status = "stopped"
            else:
                status = "error" if state == "restarting" else "stopped"

            # Map Docker service names to our naming convention
            if svc_name == "proxy":
                name = "proxy"
                topic = None
            elif svc_name.startswith("skill-server-"):
                name = f"server-{svc_name.replace('skill-server-', '')}"
                folder = svc_name.replace("skill-server-", "")
                topic = f"TOPIC_{folder.upper().replace('-', '_')}"
            elif svc_name == "agent":
                name = "agent"
                topic = None
            else:
                continue

            result_list.append({
                "name": name,
                "status": status,
                "pid": None,
                "topic": topic,
                "skills_dir": None,
                "matcher": "llm",
                "log_file": None,
                "started_at": None,
                "docker": True,
                "docker_service": svc_name,
                "docker_health": health or None,
            })
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return result_list


def _docker_compose_cmd(action: str, service: str | None = None, extra: list[str] | None = None) -> dict:
    """Run a docker compose command and return result."""
    cmd = ["docker", "compose", action]
    if extra:
        cmd.extend(extra)
    if service:
        cmd.append(service)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "stdout": result.stdout[-500:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "stderr": "Command timed out"}
    except FileNotFoundError:
        return {"status": "error", "stderr": "docker compose not found"}


def _get_docker_logs(service: str, tail: int = 200) -> list[str]:
    """Get logs from a Docker container."""
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--tail", str(tail), "--no-color", service],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            return result.stdout.splitlines()
        return [f"Error fetching logs: {result.stderr}"]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return [f"Error: {e}"]


# ══════════════════════════════════════════════════════════
#  Chat agent state
# ══════════════════════════════════════════════════════════
_agent = None
_discovery = None
_agent_lock = asyncio.Lock()


def _detect_python() -> str:
    for candidate in [PROJECT_ROOT / ".venv" / "bin" / "python3",
                      PROJECT_ROOT / "venv" / "bin" / "python3"]:
        if candidate.exists():
            return str(candidate)
    return "python3"


# ── LLM Router with tracing ──
ROUTER_SYSTEM_PROMPT = """\
You are a routing classifier for a distributed skill system.

You will be given:
1. A list of available topics with their descriptions and skills.
2. A user intent (task description).

Your job is to decide which TOPIC the intent should be routed to.

Rules:
- Reply with ONLY the topic name (e.g., TOPIC_DATA_PROCESSING).
- Do NOT include any explanation, punctuation, or extra text.
- Choose the single best-matching topic based on the intent.
"""


def route_to_topic_llm(user_intent, topics, metadata_summary):
    """Route intent to topic via LLM; returns (topic, TraceSpan)."""
    from llm_utils import chat as llm_chat

    topic_names = [tm.topic for tm in topics]
    user_msg = (
        f"Available topics and skills:\n{metadata_summary}\n\n"
        f"Valid topic names: {', '.join(topic_names)}\n\n"
        f"User intent: {user_intent}\n\n"
        f"Which topic should this intent be routed to? "
        f"Reply with ONLY the topic name."
    )

    span = TraceSpan(
        name="LLM Topic Routing",
        phase="routing",
        start_ms=time.time() * 1000,
        details={
            "system_prompt": ROUTER_SYSTEM_PROMPT[:300],
            "user_prompt": user_msg,
            "available_topics": topic_names,
        },
    )

    try:
        reply = llm_chat(
            ROUTER_SYSTEM_PROMPT,
            user_msg,
            max_tokens=1024,
            temperature=0.0,
        ).strip()

        chosen = topic_names[0] if topic_names else ""
        for tn in topic_names:
            if tn in reply:
                chosen = tn
                break

        span.finish({"llm_raw_response": reply, "chosen_topic": chosen})
        return chosen, span

    except Exception as e:
        span.finish({"error": str(e)})
        return topic_names[0] if topic_names else "", span


def _get_discovery():
    global _discovery
    if _discovery is None:
        from skillscale.discovery import SkillDiscovery
        _discovery = SkillDiscovery(
            skills_root=str(SKILLS_DIR),
            topic_descriptions={
                "TOPIC_DATA_PROCESSING": "Data processing and analysis skills",
                "TOPIC_CODE_ANALYSIS": "Code analysis and review skills",
            },
        ).scan()
    return _discovery


async def _get_agent():
    global _agent
    async with _agent_lock:
        if _agent is None:
            from agent.main import SkillScaleAgent, AgentConfig
            config = AgentConfig.from_env()
            _agent = SkillScaleAgent(config)
            await _agent.start()
        return _agent


# ══════════════════════════════════════════════════════════
#  Helpers (management)
# ══════════════════════════════════════════════════════════
def _scan_skills_folder(folder: Path) -> dict:
    skills = []
    agents_md = folder / "AGENTS.md"

    if agents_md.exists():
        content = agents_md.read_text()
        for match in re.finditer(
            r"<skill>\s*<name>(.*?)</name>.*?<description>(.*?)</description>",
            content, re.DOTALL
        ):
            name = match.group(1).strip()
            desc = match.group(2).strip().replace("\n", " ")
            desc = re.sub(r"\s+", " ", desc)
            skill_dir = folder / name
            skills.append({
                "name": name,
                "description": desc,
                "has_run_script": (skill_dir / "scripts" / "run.py").exists(),
                "has_skill_md": (skill_dir / "SKILL.md").exists(),
            })
    else:
        for sub in sorted(folder.iterdir()):
            if not sub.is_dir() or sub.name.startswith((".", "_")):
                continue
            skill_md = sub / "SKILL.md"
            if skill_md.exists():
                text = skill_md.read_text()
                name = sub.name
                desc = ""
                for line in text.splitlines():
                    if line.strip().startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip("\"'")
                        break
                skills.append({
                    "name": name,
                    "description": desc,
                    "has_run_script": (sub / "scripts" / "run.py").exists(),
                    "has_skill_md": True,
                })

    return {
        "folder": folder.name,
        "path": str(folder),
        "has_agents_md": agents_md.exists(),
        "skills": skills,
    }


def _is_process_alive(proc):
    if proc is None:
        return False
    return proc.poll() is None


def _update_service_status(svc):
    if svc.process and _is_process_alive(svc.process):
        svc.status = "running"
        svc.pid = svc.process.pid
    else:
        svc.status = "stopped"
        svc.pid = None


# ══════════════════════════════════════════════════════════
#  API Models
# ══════════════════════════════════════════════════════════
class LaunchRequest(BaseModel):
    topic: str
    skills_dir: str
    matcher: str = "llm"
    description: str = ""
    workers: int = 2
    timeout_ms: int = 180000


class LaunchProxyRequest(BaseModel):
    xsub_port: int = 5444
    xpub_port: int = 5555


class ChatRequest(BaseModel):
    message: str
    topic: Optional[str] = None
    timeout: float = 180.0


# ══════════════════════════════════════════════════════════
#  Management API Routes
# ══════════════════════════════════════════════════════════
@app.get("/api/skills")
def list_skill_folders():
    folders = []
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith((".", "_")):
            folders.append(_scan_skills_folder(entry))
    return {"skills_root": str(SKILLS_DIR), "folders": folders}


@app.get("/api/services")
def list_services():
    # Check Docker mode first
    docker_services = _get_docker_services()
    if docker_services:
        return {"services": docker_services, "docker_mode": True}

    # Fall back to local process tracking
    result = []
    for name, svc in services.items():
        _update_service_status(svc)
        result.append({
            "name": name,
            "status": svc.status,
            "pid": svc.pid,
            "topic": svc.topic,
            "skills_dir": svc.skills_dir,
            "matcher": svc.matcher,
            "log_file": svc.log_file,
            "started_at": svc.started_at,
            "docker": False,
        })
    return {"services": result, "docker_mode": False}


@app.post("/api/proxy/launch")
def launch_proxy(req: LaunchProxyRequest = LaunchProxyRequest()):
    # Docker mode: start proxy container
    if _detect_docker_mode():
        result = _docker_compose_cmd("start", "proxy")
        return {"status": "started", "docker": True, **result}

    if "proxy" in services:
        _update_service_status(services["proxy"])
        if services["proxy"].status == "running":
            raise HTTPException(400, "Proxy is already running")

    if not PROXY_BIN.exists():
        raise HTTPException(404, f"Proxy binary not found: {PROXY_BIN}")

    log_file = "/tmp/skillscale_proxy.log"
    env = {
        **os.environ,
        "SKILLSCALE_XSUB_PORT": str(req.xsub_port),
        "SKILLSCALE_XPUB_PORT": str(req.xpub_port),
    }
    proc = subprocess.Popen(
        [str(PROXY_BIN)],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    services["proxy"] = ServiceInfo(
        name="proxy",
        pid=proc.pid,
        process=proc,
        log_file=log_file,
        status="running",
        started_at=time.time(),
    )
    return {"status": "started", "pid": proc.pid, "log_file": log_file}


@app.post("/api/server/launch")
def launch_server(req: LaunchRequest):
    svc_name = f"server-{req.topic.lower().replace('topic_', '')}"

    # Docker mode: start the corresponding Docker container
    if _detect_docker_mode():
        folder = req.topic.lower().replace("topic_", "").replace("_", "-")
        docker_svc = f"skill-server-{folder}"
        result = _docker_compose_cmd("start", docker_svc)
        return {"status": "started", "name": svc_name, "docker": True, **result}

    if svc_name in services:
        _update_service_status(services[svc_name])
        if services[svc_name].status == "running":
            raise HTTPException(400, f"Server '{svc_name}' is already running")

    if not SERVER_BIN.exists():
        raise HTTPException(404, f"Skill server binary not found: {SERVER_BIN}")

    python_path = _detect_python()
    log_file = f"/tmp/skillscale_{svc_name}.log"

    cmd = [
        str(SERVER_BIN),
        "--topic", req.topic,
        "--skills-dir", req.skills_dir,
        "--matcher", req.matcher,
        "--python", python_path,
        "--workers", str(req.workers),
        "--timeout", str(req.timeout_ms),
    ]
    if req.description:
        cmd.extend(["--description", req.description])

    proc = subprocess.Popen(
        cmd,
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )
    services[svc_name] = ServiceInfo(
        name=svc_name,
        pid=proc.pid,
        process=proc,
        log_file=log_file,
        topic=req.topic,
        skills_dir=req.skills_dir,
        matcher=req.matcher,
        status="running",
        started_at=time.time(),
    )
    return {"status": "started", "name": svc_name, "pid": proc.pid, "log_file": log_file}


@app.post("/api/services/{name}/stop")
def stop_service(name: str):
    # Docker mode: stop the Docker container
    if _detect_docker_mode():
        docker_services = _get_docker_services()
        docker_svc = None
        for ds in docker_services:
            if ds["name"] == name:
                docker_svc = ds.get("docker_service")
                break
        if not docker_svc:
            raise HTTPException(404, f"Service '{name}' not found in Docker")
        result = _docker_compose_cmd("stop", docker_svc)
        return {"status": "stopped", "name": name, "docker": True, **result}

    if name not in services:
        raise HTTPException(404, f"Service '{name}' not found")

    svc = services[name]
    _update_service_status(svc)
    if svc.status != "running":
        raise HTTPException(400, f"Service '{name}' is not running")

    svc.process.terminate()
    try:
        svc.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        svc.process.kill()
        svc.process.wait()

    svc.status = "stopped"
    svc.pid = None
    return {"status": "stopped", "name": name}


@app.post("/api/services/{name}/restart")
def restart_service(name: str):
    # Docker mode: restart the Docker container
    if _detect_docker_mode():
        docker_services = _get_docker_services()
        docker_svc = None
        for ds in docker_services:
            if ds["name"] == name:
                docker_svc = ds.get("docker_service")
                break
        if not docker_svc:
            raise HTTPException(404, f"Service '{name}' not found in Docker")
        result = _docker_compose_cmd("restart", docker_svc)
        return {"status": "restarted", "name": name, "docker": True, **result}

    if name not in services:
        raise HTTPException(404, f"Service '{name}' not found")

    svc = services[name]
    _update_service_status(svc)
    if svc.status == "running":
        svc.process.terminate()
        try:
            svc.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            svc.process.kill()
            svc.process.wait()

    if name == "proxy":
        return launch_proxy()

    return launch_server(LaunchRequest(
        topic=svc.topic or "TOPIC_DEFAULT",
        skills_dir=svc.skills_dir or str(SKILLS_DIR),
        matcher=svc.matcher,
    ))


@app.get("/api/services/{name}/logs")
def get_logs(name: str, tail: int = 200):
    # Docker mode: fetch logs from Docker container
    if _detect_docker_mode():
        docker_services = _get_docker_services()
        docker_svc = None
        for ds in docker_services:
            if ds["name"] == name:
                docker_svc = ds.get("docker_service")
                break
        if docker_svc:
            lines = _get_docker_logs(docker_svc, tail=tail)
            return {"name": name, "lines": lines, "docker": True}

    if name not in services:
        raise HTTPException(404, f"Service '{name}' not found")

    svc = services[name]
    if not svc.log_file or not Path(svc.log_file).exists():
        return {"name": name, "lines": []}

    lines = Path(svc.log_file).read_text().splitlines()
    return {"name": name, "lines": lines[-tail:]}


@app.get("/api/services/{name}/logs/stream")
async def stream_logs(name: str):
    if name not in services:
        raise HTTPException(404, f"Service '{name}' not found")

    svc = services[name]
    if not svc.log_file:
        raise HTTPException(400, "No log file for this service")

    async def event_generator():
        path = Path(svc.log_file)
        last_pos = 0
        if path.exists():
            last_pos = path.stat().st_size

        while True:
            if path.exists():
                size = path.stat().st_size
                if size > last_pos:
                    with open(path) as f:
                        f.seek(last_pos)
                        new_data = f.read()
                        last_pos = f.tell()
                        for line in new_data.splitlines():
                            yield f"data: {json.dumps(line)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )


@app.post("/api/launch-all")
def launch_all():
    # Docker mode: start all containers
    if _detect_docker_mode():
        result = _docker_compose_cmd("start")
        return {"results": [{"service": "all", "docker": True, **result}]}

    results = []

    proxy_info = services.get("proxy")
    if proxy_info is None or proxy_info.status != "running":
        try:
            proxy_result = launch_proxy()
            results.append({"service": "proxy", **proxy_result})
        except HTTPException:
            results.append({"service": "proxy", "status": "already running"})
        time.sleep(1)
    else:
        results.append({"service": "proxy", "status": "already running"})

    # Dynamically scan skill folders instead of hardcoding
    for entry in sorted(SKILLS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        if not (entry / "AGENTS.md").exists():
            continue

        folder_name = entry.name
        topic = f"TOPIC_{folder_name.upper().replace('-', '_')}"
        svc_name = f"server-{folder_name}"

        if svc_name in services:
            _update_service_status(services[svc_name])
            if services[svc_name].status == "running":
                results.append({"service": svc_name, "status": "already running"})
                continue

        try:
            result = launch_server(LaunchRequest(
                topic=topic,
                skills_dir=str(entry),
                description=f"{folder_name} server",
            ))
            results.append({"service": svc_name, **result})
        except HTTPException as e:
            results.append({"service": svc_name, "status": "error", "error": str(e.detail)})

    return {"results": results}


@app.post("/api/stop-all")
def stop_all():
    # Docker mode: stop all containers
    if _detect_docker_mode():
        result = _docker_compose_cmd("stop")
        return {"results": [{"service": "all", "docker": True, **result}]}

    results = []
    for name in list(services.keys()):
        _update_service_status(services[name])
        if services[name].status == "running":
            try:
                result = stop_service(name)
                results.append({"service": name, **result})
            except Exception as e:
                results.append({"service": name, "status": "error", "error": str(e)})
        else:
            results.append({"service": name, "status": "already stopped"})
    return {"results": results}


@app.get("/api/config")
def get_config():
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    docker_mode = _detect_docker_mode()
    return {
        "project_root": str(PROJECT_ROOT),
        "skills_dir": str(SKILLS_DIR),
        "proxy_binary": str(PROXY_BIN),
        "server_binary": str(SERVER_BIN),
        "proxy_exists": PROXY_BIN.exists(),
        "server_exists": SERVER_BIN.exists(),
        "python": _detect_python(),
        "llm_provider": os.getenv("LLM_PROVIDER", "azure"),
        "llm_model": os.getenv(
            f"{os.getenv('LLM_PROVIDER', 'azure').upper()}_MODEL", "unknown"
        ),
        "docker_mode": docker_mode,
    }


# ══════════════════════════════════════════════════════════
#  Chat API with Tracing
# ══════════════════════════════════════════════════════════
@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    """Send a message through the pipeline with full tracing."""
    trace = ChatTrace(
        trace_id=uuid.uuid4().hex[:12],
        message=req.message,
    )
    overall_start = time.time()

    # 1. Discovery
    discovery = _get_discovery()
    topics = discovery.list_topic_metadata()
    if not topics:
        raise HTTPException(503, "No topics discovered. Are skills installed?")

    # 2. Topic routing (with trace)
    if req.topic:
        topic = req.topic
        routing_span = TraceSpan(
            name="Manual Topic Selection",
            phase="routing",
            start_ms=time.time() * 1000,
            details={"manual_topic": topic, "routed_by": "manual"},
        )
        routing_span.finish()
    else:
        topic, routing_span = route_to_topic_llm(
            req.message, topics, discovery.metadata_summary()
        )

    trace.topic = topic
    trace.add_span(routing_span)

    # 3. ZMQ publish (with trace)
    zmq_span = TraceSpan(
        name="ZeroMQ Publish & Await",
        phase="zmq",
        start_ms=time.time() * 1000,
        details={
            "topic": topic,
            "proxy_xsub": "tcp://127.0.0.1:5444",
            "proxy_xpub": "tcp://127.0.0.1:5555",
            "message_size_bytes": len(req.message.encode()),
        },
    )

    agent = await _get_agent()
    raw_response = {}
    try:
        raw_response = await agent.publish_raw(topic, req.message, timeout=req.timeout)
        result = raw_response.get("content", "")
        zmq_span.finish({
            "status": "success",
            "response_size_bytes": len(result.encode()) if result else 0,
        })
    except asyncio.TimeoutError:
        zmq_span.finish({"status": "timeout"})
        trace.status = "error"
        trace.result = f"Timeout: {topic} did not respond within {req.timeout}s"
        trace.total_ms = round((time.time() - overall_start) * 1000, 1)
        trace.add_span(zmq_span)
        _traces.insert(0, trace)
        if len(_traces) > MAX_TRACES:
            _traces.pop()
        raise HTTPException(504, trace.result)
    except RuntimeError as e:
        zmq_span.finish({"status": "error", "error": str(e)})
        trace.status = "error"
        trace.result = str(e)
        trace.total_ms = round((time.time() - overall_start) * 1000, 1)
        trace.add_span(zmq_span)
        _traces.insert(0, trace)
        if len(_traces) > MAX_TRACES:
            _traces.pop()
        raise HTTPException(502, f"Skill server error: {e}")

    trace.add_span(zmq_span)

    # 4. Build skill_server + skill_exec spans from _trace metadata
    #    (sent inline by the C++ skill server in the ZMQ response)
    trace_meta = raw_response.get("_trace", {})
    if trace_meta:
        server_spans = _build_spans_from_trace(trace_meta, topic)
        for sp in server_spans:
            trace.add_span(sp)
    else:
        # Fallback: scrape C++ log files for older servers without _trace
        try:
            server_spans = _parse_server_logs_for_topic(topic)
            for sp in server_spans:
                trace.add_span(sp)
        except Exception:
            pass

    trace.result = result
    trace.status = "success"
    trace.total_ms = round((time.time() - overall_start) * 1000, 1)

    _traces.insert(0, trace)
    if len(_traces) > MAX_TRACES:
        _traces.pop()

    return {
        "id": trace.trace_id,
        "role": "assistant",
        "message": result,
        "topic": topic,
        "elapsed_ms": trace.total_ms,
        "routed_by": routing_span.details.get("routed_by", "llm"),
        "trace_id": trace.trace_id,
    }


def _build_spans_from_trace(trace_meta: dict, topic: str) -> list[TraceSpan]:
    """Build skill_server + skill_exec TraceSpans from the C++ _trace metadata."""
    spans = []

    skill_name = trace_meta.get("skill_name", "unknown")
    matcher_mode = trace_meta.get("matcher_mode", "unknown")
    exec_logs = trace_meta.get("exec_logs", [])
    elapsed_ms = trace_meta.get("elapsed_ms", 0)
    exit_code = trace_meta.get("exit_code", -1)
    stderr = trace_meta.get("stderr", "")
    execution_method = trace_meta.get("execution_method", "")

    # Skill Server span (matching + dispatch)
    server_details = {
        "topic": topic,
        "matcher_mode": matcher_mode,
        "skill_matched": skill_name,
        "exec_logs": exec_logs,
    }
    spans.append(TraceSpan(
        name=f"Skill Server ({topic})",
        phase="skill_server",
        details=server_details,
    ))

    # Skill Execution span
    exec_details = {
        "skill_name": skill_name,
        "execution_method": execution_method,
        "exit_code": exit_code,
        "elapsed_ms": elapsed_ms,
    }
    if stderr:
        exec_details["stderr"] = stderr
    exec_details["exec_logs"] = exec_logs

    spans.append(TraceSpan(
        name=f"Skill Execution ({skill_name})",
        phase="skill_exec",
        duration_ms=elapsed_ms,
        details=exec_details,
    ))

    return spans


def _parse_server_logs_for_topic(topic: str) -> list[TraceSpan]:
    """Parse the most recent request block from C++ skill server logs."""
    spans = []

    for name, svc in services.items():
        if not svc.log_file or not Path(svc.log_file).exists():
            continue
        if not name.startswith("server-"):
            continue

        try:
            lines = Path(svc.log_file).read_text().splitlines()
        except Exception:
            continue

        # Get the last request block from recent lines
        recent = lines[-80:]

        # Find last "Processing request" line
        proc_indices = [i for i, l in enumerate(recent) if "Processing request" in l]
        if not proc_indices:
            continue

        idx = proc_indices[-1]
        block = recent[idx:]

        worker_details = {"server": name, "raw_logs": []}
        exec_details = {"server": name}
        exec_ms = 0

        for line in block:
            stripped = line.strip()
            worker_details["raw_logs"].append(stripped)

            if "Processing request" in line:
                worker_details["request_received"] = stripped
            if "Mode 2" in line or "Mode 1" in line:
                worker_details["matching_mode"] = stripped
            if "Fallback" in line or "single skill" in line:
                worker_details["fallback"] = stripped
            if "Progressive disclosure" in line:
                worker_details["progressive_disclosure"] = stripped
            if "Executing skill:" in line:
                exec_details["skill_name"] = line.split("Executing skill:")[1].strip()
            if "Intent:" in line and "executor" in line:
                exec_details["intent_preview"] = line.split("Intent:")[1].strip()
            if "Found scripts/run" in line:
                exec_details["execution_method"] = stripped
            if "Finished" in line and "exit=" in line:
                exec_details["finish_info"] = stripped
                m = re.search(r"(\d+)ms", line)
                if m:
                    exec_ms = int(m.group(1))
                    exec_details["execution_time_ms"] = exec_ms
                m2 = re.search(r"exit=(\d+)", line)
                if m2:
                    exec_details["exit_code"] = int(m2.group(1))
            if "Published response" in line:
                exec_details["response_published"] = stripped

        spans.append(TraceSpan(
            name=f"Skill Server ({name})",
            phase="skill_server",
            details=worker_details,
        ))

        skill_name = exec_details.get("skill_name", "unknown")
        spans.append(TraceSpan(
            name=f"Skill Execution ({skill_name})",
            phase="skill_exec",
            duration_ms=exec_ms,
            details=exec_details,
        ))

    return spans


@app.get("/api/topics")
def list_topics():
    discovery = _get_discovery()
    return {
        "topics": [
            {
                "topic": tm.topic,
                "description": tm.description,
                "skills": [{"name": s.name, "description": s.description} for s in tm.skills],
            }
            for tm in discovery.list_topic_metadata()
        ]
    }


@app.post("/api/discovery/refresh")
def refresh_discovery():
    global _discovery
    _discovery = None
    discovery = _get_discovery()
    return {
        "topics": discovery.list_topics(),
        "total_skills": len(discovery.list_skills()),
    }


@app.get("/api/traces")
def list_traces():
    return {"traces": [t.to_dict() for t in _traces]}


@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str):
    for t in _traces:
        if t.trace_id == trace_id:
            return t.to_dict()
    raise HTTPException(404, "Trace not found")


@app.on_event("shutdown")
async def shutdown():
    global _agent
    if _agent:
        await _agent.stop()
        _agent = None

    for name in list(services.keys()):
        svc = services[name]
        if svc.process and _is_process_alive(svc.process):
            svc.process.terminate()
            try:
                svc.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                svc.process.kill()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8401, log_level="info")
