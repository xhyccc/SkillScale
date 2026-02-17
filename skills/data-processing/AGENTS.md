# Data Processing Skill Server

This skill server handles data processing tasks including text summarization
and CSV data analysis. Skills are loaded on demand using the OpenSkills
SKILL.md format.

## Available Skills

<available_skills>

<skill>
  <name>text-summarizer</name>
  <description>
    Summarizes text input using LLM-powered analysis. Extracts key themes,
    main arguments, and produces concise structured summaries of articles,
    documents, and multi-paragraph text.
  </description>
  <location>text-summarizer/</location>
</skill>

<skill>
  <name>csv-analyzer</name>
  <description>
    Analyzes CSV data using LLM-powered insights on top of statistical
    computation. Produces column statistics, pattern detection, anomaly
    identification, and natural-language data insights.
  </description>
  <location>csv-analyzer/</location>
</skill>

</available_skills>

## Invocation

Skills are invoked by the skill server when it receives a task-based intent
on the `TOPIC_DATA_PROCESSING` ZeroMQ topic. The server uses LLM-powered
matching to select the best skill for each incoming task, loads the SKILL.md
for full instructions, and executes `scripts/run.py`.
