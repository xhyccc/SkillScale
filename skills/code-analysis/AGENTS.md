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

<skills_system priority="1">

## Available Skills

<!-- SKILLS_TABLE_START -->
<usage>
When users ask you to perform tasks, check if any of the available skills below can help complete the task more effectively. Skills provide specialized capabilities and domain knowledge.

How to use skills:
- Invoke: `npx openskills read <skill-name>` (run in your shell)
  - For multiple: `npx openskills read skill-one,skill-two`
- The skill content will load with detailed instructions on how to complete the task
- Base directory provided in output for resolving bundled resources (references/, scripts/, assets/)

Usage notes:
- Only use skills listed in <available_skills> below
- Do not invoke a skill that is already loaded in your context
- Each skill invocation is stateless
</usage>

<available_skills>

<skill>
<name>code-complexity</name>
<description>Analyzes Python source code complexity using AST metrics and LLM-powered review. Computes cyclomatic complexity, nesting depth, and function length, then provides intelligent refactoring suggestions via LLM.</description>
<location>project</location>
</skill>

<skill>
<name>csv-analyzer</name>
<description>Analyzes CSV data using LLM-powered insights on top of statistical computation. Produces column statistics, pattern detection, anomaly identification, and natural-language data insights.</description>
<location>project</location>
</skill>

<skill>
<name>dead-code-detector</name>
<description>Detects dead code in Python source using AST analysis and LLM-powered review. Finds unused imports, unused variables, unreachable code, and empty functions, then provides intelligent cleanup suggestions via LLM.</description>
<location>project</location>
</skill>

<skill>
<name>text-summarizer</name>
<description>Summarizes text input using LLM-powered analysis. Extracts key themes, main arguments, and produces concise structured summaries of articles, documents, and multi-paragraph text.</description>
<location>project</location>
</skill>

</available_skills>
<!-- SKILLS_TABLE_END -->

</skills_system>
