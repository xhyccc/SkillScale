#!/usr/bin/env python3
"""
Text Summarizer — Extractive summarization using word frequency scoring.
Reads intent from SKILLSCALE_INTENT env var or stdin.
Outputs markdown summary to stdout.
"""

import os
import re
import sys
from collections import Counter

STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "it", "its",
    "this", "that", "these", "those", "i", "you", "he", "she", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "his",
    "our", "their", "not", "no", "so", "if", "then", "than", "also",
    "just", "about", "more", "very", "all", "any", "each", "every",
    "both", "few", "many", "much", "some", "such", "only", "own",
    "same", "other", "into", "over", "after", "before", "between",
})


def split_sentences(text: str) -> list[str]:
    """Split text into sentences using regex."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def word_frequencies(text: str) -> Counter:
    """Compute word frequency counts, excluding stopwords."""
    words = re.findall(r'\b[a-z]+\b', text.lower())
    return Counter(w for w in words if w not in STOPWORDS and len(w) > 2)


def summarize(text: str, num_sentences: int = 3) -> str:
    """Extract the top N most important sentences."""
    sentences = split_sentences(text)
    if len(sentences) <= num_sentences:
        return text

    freq = word_frequencies(text)
    if not freq:
        return "\n".join(sentences[:num_sentences])

    # Score each sentence
    scores = []
    for i, sent in enumerate(sentences):
        words = re.findall(r'\b[a-z]+\b', sent.lower())
        score = sum(freq.get(w, 0) for w in words)
        scores.append((score, i, sent))

    # Take top N by score, return in original order
    top = sorted(scores, key=lambda x: x[0], reverse=True)[:num_sentences]
    top_ordered = sorted(top, key=lambda x: x[1])

    return "\n\n".join(s[2] for s in top_ordered)


def main():
    # Read input from env var or stdin
    text = os.environ.get("SKILLSCALE_INTENT", "")
    if not text:
        text = sys.stdin.read()

    if not text.strip():
        print("**Error:** No input text provided.", file=sys.stderr)
        sys.exit(1)

    if len(text) > 100_000:
        print("**Error:** Input exceeds 100,000 character limit.", file=sys.stderr)
        sys.exit(1)

    summary = summarize(text)

    # Output as markdown
    print("## Summary\n")
    print(summary)
    print(f"\n---\n*Extracted {len(split_sentences(summary))} key sentences "
          f"from {len(split_sentences(text))} total.*")


if __name__ == "__main__":
    main()
