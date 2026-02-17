---
name: csv-analyzer
description: Analyzes CSV data using LLM-powered insights on top of statistical computation. Produces column statistics, pattern detection, anomaly identification, and natural-language data insights.
---

# CSV Analyzer Skill

## Purpose
Provide both statistical analysis and LLM-powered insights for CSV
data. Computes basic stats algorithmically, then uses the LLM to
identify patterns, anomalies, and produce human-readable analysis.

## Invocation
Pass CSV data via stdin or `SKILLSCALE_INTENT` environment variable.
The first line must be a header row.

## Output
- Per-column statistics (type, count, min/max/mean/median)
- LLM-generated data insights and pattern analysis
- Anomaly detection and recommendations

## Limitations
- Maximum 50,000 rows.
- Quality depends on LLM provider configured in `.env`.
