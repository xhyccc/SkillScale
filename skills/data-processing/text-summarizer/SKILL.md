---
name: text-summarizer
description: Summarizes text input using LLM-powered analysis. Extracts key themes, main arguments, and produces concise structured summaries of articles, documents, and multi-paragraph text.
---

# Text Summarizer Skill

## Purpose
Perform intelligent text summarization using an LLM. Produces structured
summaries with key themes, main points, and a concise abstract. Handles
articles, reports, technical documents, and general prose.

## Invocation
Pass text via `SKILLSCALE_INTENT` environment variable or stdin.
Outputs a markdown-formatted summary to stdout.

## Approach
1. Pre-process text (word count, sentence count, basic stats).
2. Send to LLM with a specialized summarization prompt.
3. LLM produces structured summary with key themes and takeaways.

## Output
- Word/sentence statistics
- Key themes identified
- Structured summary with main points
- One-paragraph abstract

## Limitations
- Maximum input: 100,000 characters.
- Quality depends on LLM provider configured in `.env`.
