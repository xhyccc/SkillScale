#!/usr/bin/env python3
"""
SkillScale Management UI — FastAPI Backend

Provides REST APIs for:
- Listing skill server folders and their skills
- Launching / stopping / restarting skill servers and the proxy
- Streaming log output
- Viewing service status
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
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

app = FastAPI(title="SkillScale Management", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── State tracking ──
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


# ── Helpers ──

def _detect_python() -> str:
    for candidate in [PROJECT_ROOT / ".venv" / "bin" / "python3",
                      PROJECT_ROOT / "venv" / "bin" / "python3"]:
        if candidate.exists():
            return str(candidate)
    return "python3"


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


def _is_process_alive(proc: Optional[subprocess.Popen]) -> bool:
    if proc is None:
        return False
    return proc.poll() is None


def _update_service_status(svc: ServiceInfo):
    if svc.process and _is_process_alive(svc.process):
        svc.status = "running"
        svc.pid = svc.process.pid
    else:
        svc.status = "stopped"
        svc.pid = None


# ── API Models ──

class LaunchRequest(BaseModel):
    topic: str
    skills_dir: str
    matcher: str = "llm"
    description: str = ""
    workers: int = 2
    timeout_ms: int = 30000


class LaunchProxyRequest(BaseModel):
    xsub_port: int = 5444
    xpub_port: int = 5555


# ── API Routes ──

@app.get("/api/skills")
def list_skill_folders():
    folders = []
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.is_dir() and not entry.name.startswith((".", "_")):
            folders.append(_scan_skills_folder(entry))
    return {"skills_root": str(SKILLS_DIR), "folders": folders}


@app.get("/api/services")
def list_services():
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
        })
    return {"services": result}


@app.post("/api/proxy/launch")
def launch_proxy(req: LaunchProxyRequest = LaunchProxyRequest()):
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
    results = []

    if "proxy" not in services or services.get("proxy", ServiceInfo("")).status != "running":
        try:
            proxy_result = launch_proxy()
            results.append({"service": "proxy", **proxy_result})
        except HTTPException:
            results.append({"service": "proxy", "status": "already running"})
        time.sleep(1)
    else:
        results.append({"service": "proxy", "status": "already running"})

    topic_map = {
        "data-processing": {
            "topic": "TOPIC_DATA_PROCESSING",
            "description": "Data processing server",
        },
        "code-analysis": {
            "topic": "TOPIC_CODE_ANALYSIS",
            "description": "Code analysis server",
        },
    }

    for folder_name, cfg in topic_map.items():
        skills_path = SKILLS_DIR / folder_name
        if not skills_path.exists():
            continue

        svc_name = f"server-{folder_name}"
        if svc_name in services:
            _update_service_status(services[svc_name])
            if services[svc_name].status == "running":
                results.append({"service": svc_name, "status": "already running"})
                continue

        try:
            result = launch_server(LaunchRequest(
                topic=cfg["topic"],
                skills_dir=str(skills_path),
                description=cfg["description"],
            ))
            results.append({"service": svc_name, **result})
        except HTTPException as e:
            results.append({"service": svc_name, "status": "error", "error": str(e.detail)})

    return {"results": results}


@app.post("/api/stop-all")
def stop_all():
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
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8401, log_level="info")
