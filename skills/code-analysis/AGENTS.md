# Code Analysis Skill Server

This skill server handles code analysis tasks including complexity metrics,
dead code detection, and Python static analysis. Skills are loaded on demand
using the OpenSkills SKILL.md format.

## Available Skills

<available_skills>

<skill>
  <name>code-complexity</name>
  <description>
    Analyzes Python source code complexity using AST metrics and LLM-powered
    review. Computes cyclomatic complexity, nesting depth, and function length,
    then provides intelligent refactoring suggestions via LLM.
  </description>
  <location>code-complexity/</location>
</skill>

<skill>
  <name>dead-code-detector</name>
  <description>
    Detects dead code in Python source using AST analysis and LLM-powered
    review. Finds unused imports, unused variables, unreachable code, and
    empty functions, then provides intelligent cleanup suggestions via LLM.
  </description>
  <location>dead-code-detector/</location>
</skill>

</available_skills>

## Invocation

Skills are invoked by the skill server when it receives a task-based intent
on the `TOPIC_CODE_ANALYSIS` ZeroMQ topic. The server uses LLM-powered
matching to select the best skill for each incoming task, loads the SKILL.md
for full instructions, and executes `scripts/run.py`.
