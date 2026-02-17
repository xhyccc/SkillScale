#!/usr/bin/env python3
"""
LLM-powered skill matcher for the C++ skill server.

Called as a subprocess by the C++ skill server when --matcher=llm.

Input (stdin JSON):
    {
        "task": "summarize this article about ...",
        "skills": [
            {"name": "text-summarizer", "description": "Summarizes text ..."},
            {"name": "csv-analyzer", "description": "Analyzes CSV data ..."}
        ],
        "prompt_file": "/path/to/prompts/skill_match.txt"   (optional)
    }

Output (stdout): the matched skill name, or "none" if no match.

Uses the shared skills/llm_utils.py for LLM connectivity.
"""

import json
import sys
import os
from pathlib import Path

# Ensure the project root's skills/ dir is importable
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "skills"))

from llm_utils import chat  # noqa: E402

# ── Default prompt template ──
_DEFAULT_PROMPT = _SCRIPT_DIR / "prompts" / "skill_match.txt"


def load_prompt(prompt_file: str | None = None) -> str:
    """Load the prompt template from file."""
    path = Path(prompt_file) if prompt_file else _DEFAULT_PROMPT
    if path.exists():
        return path.read_text()
    # Inline fallback
    return (
        "Given these skills:\n{skills}\n\n"
        "Which skill best handles this task? "
        "Reply with ONLY the skill name or 'none'.\n\n"
        "Task: {task}"
    )


def format_skills(skills: list[dict]) -> str:
    """Format skills list for the prompt."""
    lines = []
    for s in skills:
        lines.append(f"- **{s['name']}**: {s['description']}")
    return "\n".join(lines)


def match(task: str, skills: list[dict], prompt_file: str | None = None) -> str:
    """Use LLM to match a task to the best skill."""
    template = load_prompt(prompt_file)
    skills_text = format_skills(skills)

    prompt = template.replace("{skills}", skills_text).replace("{task}", task)

    # Call LLM with a system message for precision
    result = chat(
        "You are a skill router. Reply with ONLY the skill name.",
        prompt,
    )

    # Clean up the response — extract just the skill name
    result = result.strip().strip('"').strip("'").strip("`").strip()

    # Validate against known skill names
    skill_names = {s["name"].lower(): s["name"] for s in skills}
    result_lower = result.lower()

    if result_lower in skill_names:
        return skill_names[result_lower]

    # Fuzzy: check if any skill name is contained in the response
    for lower_name, original_name in skill_names.items():
        if lower_name in result_lower:
            return original_name

    return "none"


def main():
    """Read task+skills from stdin JSON, print matched skill name."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"none", file=sys.stdout)
        print(f"JSON parse error: {e}", file=sys.stderr)
        sys.exit(1)

    task = data.get("task", "")
    skills = data.get("skills", [])
    prompt_file = data.get("prompt_file", None)

    if not task or not skills:
        print("none")
        sys.exit(0)

    result = match(task, skills, prompt_file)
    print(result, end="")


if __name__ == "__main__":
    main()
