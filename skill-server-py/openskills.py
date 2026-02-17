"""
OpenSkills-compatible skill loader for SkillScale.

Implements the OpenSkills pattern:
  1. Parse AGENTS.md to discover which skills are installed
     (progressive disclosure — lightweight metadata only).
  2. On demand, read the full SKILL.md for a matched skill
     (full instructions + context).
  3. Execute scripts/run.py to invoke the skill.

This mirrors what `npx openskills read <name>` does, but natively
in Python without requiring Node.js.
"""

import os
import re
import subprocess
import sys
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

log = logging.getLogger("openskills")


# ──────────────────────────────────────────────────────────
#  Data classes
# ──────────────────────────────────────────────────────────

@dataclass
class SkillEntry:
    """Lightweight skill metadata parsed from AGENTS.md <available_skills>."""
    name: str
    description: str
    location: str           # relative path inside skills_dir


@dataclass
class SkillDetail:
    """Full skill detail loaded from SKILL.md on demand."""
    name: str
    description: str
    instructions: str       # full markdown body
    base_dir: str           # absolute path to skill directory
    script_path: str        # absolute path to scripts/run.py (or "")


@dataclass
class ExecutionResult:
    success: bool = False
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""


# ──────────────────────────────────────────────────────────
#  AGENTS.md parser — extracts <available_skills> XML block
# ──────────────────────────────────────────────────────────

def parse_agents_md(agents_md_path: str) -> List[SkillEntry]:
    """
    Parse the <available_skills> XML block from an AGENTS.md file.
    Returns a list of lightweight SkillEntry objects.
    """
    if not os.path.isfile(agents_md_path):
        log.warning("AGENTS.md not found: %s", agents_md_path)
        return []

    with open(agents_md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract the <available_skills> block
    match = re.search(
        r"<available_skills>(.*?)</available_skills>",
        content,
        re.DOTALL,
    )
    if not match:
        log.warning("No <available_skills> block in %s", agents_md_path)
        return []

    xml_text = f"<root>{match.group(1)}</root>"

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.error("Failed to parse <available_skills> XML: %s", e)
        return []

    skills: List[SkillEntry] = []
    for skill_elem in root.findall("skill"):
        name = (skill_elem.findtext("name") or "").strip()
        desc = (skill_elem.findtext("description") or "").strip()
        loc = (skill_elem.findtext("location") or "").strip()
        if name:
            skills.append(SkillEntry(name=name, description=desc, location=loc))

    log.info("Parsed %d skills from %s", len(skills), agents_md_path)
    return skills


# ──────────────────────────────────────────────────────────
#  SKILL.md reader — loads full instructions on demand
# ──────────────────────────────────────────────────────────

def read_skill_md(skills_dir: str, entry: SkillEntry) -> Optional[SkillDetail]:
    """
    Load the full SKILL.md for a given skill entry.
    This is the 'progressive disclosure' step — only called when
    the skill has been matched and needs to be invoked.
    """
    skill_dir = os.path.join(skills_dir, entry.location.rstrip("/"))
    skill_md_path = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(skill_md_path):
        log.error("SKILL.md not found: %s", skill_md_path)
        return None

    with open(skill_md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not fm_match:
        log.warning("No YAML frontmatter in %s", skill_md_path)
        return None

    yaml_block = fm_match.group(1)
    body = fm_match.group(2).strip()

    # Simple YAML parse
    fields: Dict[str, str] = {}
    for line in yaml_block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()

    # Locate scripts/run.py
    script_path = os.path.join(skill_dir, "scripts", "run.py")
    if not os.path.isfile(script_path):
        script_path = ""

    return SkillDetail(
        name=fields.get("name", entry.name),
        description=fields.get("description", entry.description),
        instructions=body,
        base_dir=os.path.abspath(skill_dir),
        script_path=os.path.abspath(script_path) if script_path else "",
    )


# ──────────────────────────────────────────────────────────
#  Skill executor — runs scripts/run.py via subprocess
# ──────────────────────────────────────────────────────────

def execute_skill(
    detail: SkillDetail,
    stdin_data: str,
    timeout_sec: int = 120,
    python_exe: str = "",
) -> ExecutionResult:
    """
    Execute a skill by running its scripts/run.py.
    Passes the intent/data on stdin and captures stdout/stderr.
    """
    if not detail.script_path:
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stderr=f"No scripts/run.py found for skill: {detail.name}",
        )

    # Resolve python executable
    if not python_exe:
        python_exe = sys.executable

    cmd = [python_exe, detail.script_path]

    log.info("[executor] Running skill: %s (%s)", detail.name, detail.script_path)

    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=detail.base_dir,
            env={**os.environ, "SKILLSCALE_INTENT": stdin_data},
        )
        return ExecutionResult(
            success=(proc.returncode == 0),
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stderr=f"Skill execution timed out after {timeout_sec}s",
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            exit_code=-1,
            stderr=f"Skill execution failed: {e}",
        )
