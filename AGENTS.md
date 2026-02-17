# SkillScale — Agent Instructions

This project is a distributed skill execution system that routes user intents
to specialised skill servers via ZeroMQ pub/sub topics. Skills follow the
[OpenSkills](https://github.com/numman-ali/openskills) / Anthropic SKILL.md
format and are loaded on demand.

## Architecture

```
User → Agent (LLM router → extracts intent → selects topic)
         ↓
       ZMQ Proxy (C++ XPUB/XSUB middleware)
         ├── TOPIC_DATA_PROCESSING  → C++ Skill Server (container)
         │     parses AGENTS.md → keyword matches skill
         │     → openskills CLI loads SKILL.md → runs scripts/run.py
         └── TOPIC_CODE_ANALYSIS    → C++ Skill Server (container)
               parses AGENTS.md → keyword matches skill
               → openskills CLI loads SKILL.md → runs scripts/run.py
```

### Component Responsibilities

| Component | Role |
|-----------|------|
| **Agent** | Reasons about user input, extracts a self-contained task description, uses LLM to route intent to the correct topic (skill server) |
| **C++ Proxy** | ZeroMQ XPUB/XSUB star-topology message broker — unchanged middleware |
| **C++ Skill Server** | Containerised per-topic server. Parses `AGENTS.md` `<available_skills>` XML for discovery (OpenSkills format). Matches incoming tasks via keyword scoring. Uses `openskills read <name>` CLI for progressive SKILL.md disclosure. Executes `scripts/run.py` via subprocess. |
| **OpenSkills CLI** | Shell script (`scripts/openskills`) implementing `list`, `read`, and `sync` commands for skill discovery and progressive loading. |
| **Skills** | Each skill has a `SKILL.md` (metadata + instructions) and `scripts/run.py` (LLM-powered execution). Skills call LLMs for intelligent analysis. |

### OpenSkills Invocation Flow

1. Skill server starts → parses `skills/<topic>/AGENTS.md` → extracts `<available_skills>` XML
2. Task arrives on ZMQ topic → skill server extracts task description
3. Keyword scoring matches task against skill descriptions from AGENTS.md
4. Matched skill's `SKILL.md` is loaded on demand (progressive disclosure)
5. `scripts/run.py` is executed with task data on stdin
6. Result is published back to the agent via ZMQ

## Available Skills

<available_skills>

<skill>
  <name>text-summarizer</name>
  <topic>TOPIC_DATA_PROCESSING</topic>
  <description>
    Summarizes text input using LLM-powered analysis. Extracts key themes,
    main arguments, and produces concise structured summaries with word/sentence
    statistics.
  </description>
  <location>skills/data-processing/text-summarizer/</location>
</skill>

<skill>
  <name>csv-analyzer</name>
  <topic>TOPIC_DATA_PROCESSING</topic>
  <description>
    Analyzes CSV data using LLM-powered insights on top of statistical
    computation. Produces column-level statistics (count, mean, min, max,
    unique values) and AI-generated data insights including patterns,
    observations, and recommendations.
  </description>
  <location>skills/data-processing/csv-analyzer/</location>
</skill>

<skill>
  <name>code-complexity</name>
  <topic>TOPIC_CODE_ANALYSIS</topic>
  <description>
    Analyzes Python source code complexity using AST metrics and LLM-powered
    review. Computes cyclomatic complexity, nesting depth, LOC per function,
    then uses an LLM for refactoring suggestions and code quality review.
  </description>
  <location>skills/code-analysis/code-complexity/</location>
</skill>

<skill>
  <name>dead-code-detector</name>
  <topic>TOPIC_CODE_ANALYSIS</topic>
  <description>
    Detects dead code in Python source using AST analysis and LLM-powered
    review. Finds unused imports, unreachable statements, unused variables,
    and empty function bodies. The LLM provides cleanup suggestions and
    highlights edge cases.
  </description>
  <location>skills/code-analysis/dead-code-detector/</location>
</skill>

</available_skills>

## Adding a New Skill

1. Create a directory under `skills/<topic-folder>/<skill-name>/`.
2. Add a `SKILL.md` with YAML frontmatter (`name`, `description`) and usage
   instructions.
3. Add `scripts/run.py` — reads from stdin, writes to stdout.
4. Register the topic folder mapping in the skill server startup and
   `SkillDiscovery.topic_descriptions`.
5. Update this `AGENTS.md` with a new `<skill>` entry.

## LLM Configuration

All skills and the routing agent share `skills/llm_utils.py`, which reads
API credentials from the project-root `.env` file. Supported providers:

| Provider | Env vars | Example model |
|----------|----------|---------------|
| `azure`  | `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_MODEL`, `AZURE_API_VERSION` | gpt-4o |
| `openai` | `OPENAI_API_KEY`, `OPENAI_API_BASE`, `OPENAI_MODEL` | DeepSeek-V3.1-Terminus |
| `zhipu`  | `ZHIPU_API_KEY`, `ZHIPU_MODEL` | GLM-4.7-FlashX |

Set `LLM_PROVIDER=azure|openai|zhipu` in `.env` to select the active provider.
