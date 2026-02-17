"""
SkillScale Skill Discovery — Scans SKILL.md files and exposes metadata.

The discovery module reads SKILL.md YAML frontmatter from a skills directory
and makes it available to agent frameworks for progressive disclosure:
frameworks see lightweight metadata (name, description, topic) without
loading the full instruction body.
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("skillscale.discovery")


@dataclass
class SkillMetadata:
    """Parsed metadata from a single SKILL.md file."""

    name: str
    description: str
    topic: str                              # inferred from directory structure
    license: str = "MIT"
    compatibility: str = ""
    allowed_tools: str = ""
    skill_dir: str = ""                     # absolute path to skill directory
    instructions: str = ""                  # markdown body (loaded on demand)

    def to_tool_description(self) -> str:
        """One-line description suitable for LLM tool schemas."""
        return f"[{self.topic}] {self.name}: {self.description}"


@dataclass
class SkillDiscovery:
    """
    Scans a skills directory tree and parses SKILL.md frontmatter.

    Expected layout:
        skills/
        ├── data-processing/          ← topic = TOPIC_DATA_PROCESSING
        │   ├── text-summarizer/
        │   │   └── SKILL.md
        │   └── csv-analyzer/
        │       └── SKILL.md
        └── code-analysis/            ← topic = TOPIC_CODE_ANALYSIS
            └── code-complexity/
                └── SKILL.md
    """

    skills_root: str
    _skills: Dict[str, SkillMetadata] = field(default_factory=dict, init=False)

    # ── Public API ─────────────────────────────────────────

    def scan(self) -> "SkillDiscovery":
        """Walk the skills directory tree and parse all SKILL.md files."""
        self._skills.clear()
        root = os.path.abspath(self.skills_root)

        if not os.path.isdir(root):
            log.warning("Skills root not found: %s", root)
            return self

        for category in sorted(os.listdir(root)):
            category_path = os.path.join(root, category)
            if not os.path.isdir(category_path):
                continue

            topic = f"TOPIC_{category.upper().replace('-', '_')}"

            for skill_name in sorted(os.listdir(category_path)):
                skill_dir = os.path.join(category_path, skill_name)
                skill_md = os.path.join(skill_dir, "SKILL.md")
                if not os.path.isfile(skill_md):
                    continue

                meta = self._parse_skill_md(skill_md, topic, skill_dir)
                if meta:
                    self._skills[meta.name] = meta
                    log.info("Discovered skill: %s (topic=%s)", meta.name, topic)

        log.info("Discovery complete: %d skills found", len(self._skills))
        return self

    def list_skills(self) -> List[SkillMetadata]:
        """Return all discovered skills."""
        return list(self._skills.values())

    def get_skill(self, name: str) -> Optional[SkillMetadata]:
        """Look up a single skill by name."""
        return self._skills.get(name)

    def skills_for_topic(self, topic: str) -> List[SkillMetadata]:
        """Return all skills registered under a given topic."""
        return [s for s in self._skills.values() if s.topic == topic]

    def list_topics(self) -> List[str]:
        """Return unique topic names."""
        return sorted({s.topic for s in self._skills.values()})

    def metadata_summary(self) -> str:
        """
        Compact summary suitable for injecting into an LLM system prompt
        (progressive disclosure — metadata layer only).
        """
        if not self._skills:
            return "No skills available."

        lines = ["Available SkillScale skills:\n"]
        by_topic: Dict[str, List[SkillMetadata]] = {}
        for s in self._skills.values():
            by_topic.setdefault(s.topic, []).append(s)

        for topic in sorted(by_topic):
            lines.append(f"  Topic: {topic}")
            for s in by_topic[topic]:
                lines.append(f"    - {s.name}: {s.description}")
            lines.append("")

        return "\n".join(lines)

    # ── Internal ───────────────────────────────────────────

    @staticmethod
    def _parse_skill_md(
        path: str, topic: str, skill_dir: str
    ) -> Optional[SkillMetadata]:
        """Parse YAML frontmatter from a SKILL.md file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            log.error("Cannot read %s: %s", path, e)
            return None

        # Extract YAML frontmatter between --- delimiters
        match = re.match(
            r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL
        )
        if not match:
            log.warning("No YAML frontmatter in %s", path)
            return None

        yaml_block = match.group(1)
        body = match.group(2).strip()

        # Lightweight YAML parse (key: value lines)
        fields: Dict[str, str] = {}
        for line in yaml_block.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                fields[key.strip()] = val.strip()

        name = fields.get("name", "")
        if not name:
            log.warning("SKILL.md missing 'name' field: %s", path)
            return None

        return SkillMetadata(
            name=name,
            description=fields.get("description", ""),
            topic=topic,
            license=fields.get("license", "MIT"),
            compatibility=fields.get("compatibility", ""),
            allowed_tools=fields.get("allowed-tools", ""),
            skill_dir=skill_dir,
            instructions=body,
        )
