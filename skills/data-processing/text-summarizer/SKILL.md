---
name: text-summarizer
description: Summarizes text input by extracting key sentences using frequency-based extractive summarization. Handles plain text, multi-paragraph documents, and structured content.
license: MIT
compatibility: python3
allowed-tools: python3
---

# Text Summarizer Skill

## Purpose
Perform extractive text summarization using word frequency analysis.
This skill does NOT use any LLM — it is a pure algorithmic summarizer
suitable for pre-processing large documents before sending to an agent.

## Invocation
The user intent is passed via `SKILLSCALE_INTENT` environment variable
or via stdin. The skill outputs a markdown-formatted summary to stdout.

## Algorithm
1. Tokenize the input into sentences.
2. Compute word frequencies (excluding stopwords).
3. Score each sentence by the sum of its word frequencies.
4. Return the top N sentences (default: 3) in original order.

## Limitations
- English text only.
- Does not handle tables or code blocks.
- Maximum input: 100,000 characters.
