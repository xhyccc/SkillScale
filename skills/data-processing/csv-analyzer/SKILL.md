---
name: csv-analyzer
description: Analyzes CSV data to produce statistical summaries including column types, counts, means, medians, and value distributions. Supports pipes and file paths.
license: MIT
compatibility: python3
allowed-tools: python3
---

# CSV Analyzer Skill

## Purpose
Provide rapid statistical analysis of CSV data without requiring
heavy dependencies like pandas. Pure Python implementation using
only the standard library.

## Invocation
Pass CSV data via stdin or set `SKILLSCALE_INTENT` to the CSV content.
The first line must be a header row.

## Output
Markdown-formatted table with per-column statistics:
- Column name and inferred type (numeric / text / date)
- Count of non-empty values
- For numeric columns: min, max, mean, median
- For text columns: unique count, most common value

## Limitations
- Maximum 50,000 rows.
- Does not support multi-line quoted fields.
- Date detection is heuristic-based.
