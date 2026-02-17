#!/usr/bin/env python3
"""
Text Summarizer — LLM-powered summarization with basic stats.
Reads intent from SKILLSCALE_INTENT env var or stdin.
Outputs markdown summary to stdout.
"""

import os
import re
import sys

# Add skills/ to path so llm_utils is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from llm_utils import chat

SYSTEM_PROMPT = """\
You are an expert text summarizer. Given a piece of text, produce a structured
markdown summary with the following sections:

## Summary

### Key Themes
- List 2-4 key themes or topics covered

### Main Points
- List the 3-5 most important points, each in one sentence

### Abstract
Write a single concise paragraph (3-4 sentences) summarizing the entire text.

---
*Summarized by SkillScale text-summarizer (LLM-powered)*

Be concise and factual. Do not add information not present in the original text.
"""


def count_stats(text: str) -> dict:
    """Basic text statistics."""
    words = text.split()
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s for s in sentences if len(s.strip()) > 5]
    return {
        "chars": len(text),
        "words": len(words),
        "sentences": len(sentences),
    }


def main():
    text = os.environ.get("SKILLSCALE_INTENT", "")
    if not text:
        text = sys.stdin.read()

    if not text.strip():
        print("**Error:** No input text provided.", file=sys.stderr)
        sys.exit(1)

    if len(text) > 100_000:
        print("**Error:** Input exceeds 100,000 character limit.", file=sys.stderr)
        sys.exit(1)

    stats = count_stats(text)

    # Truncate for LLM context if very long
    llm_input = text[:8000] if len(text) > 8000 else text

    try:
        result = chat(SYSTEM_PROMPT, llm_input, max_tokens=1024, temperature=0.3)
        print(result)
        print(f"\n*Input: {stats['words']} words, {stats['sentences']} sentences.*")
    except Exception as e:
        # Fallback: simple extractive summary if LLM fails
        print(f"## Summary\n", file=sys.stderr)
        print(f"*LLM unavailable ({e}), falling back to extractive summary.*",
              file=sys.stderr)
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        sentences = [s for s in sentences if len(s.strip()) > 10]
        top = sentences[:3] if len(sentences) > 3 else sentences
        print("## Summary\n")
        print("\n\n".join(top))
        print(f"\n---\n*Extracted {len(top)} key sentences "
              f"(LLM fallback mode).*")


if __name__ == "__main__":
    main()
