#!/usr/bin/env python3
"""
CSV Analyzer — Statistical analysis + LLM-powered insights.
Reads from SKILLSCALE_INTENT env var or stdin.
Outputs markdown-formatted analysis to stdout.
"""

import csv
import io
import os
import re
import sys
from collections import Counter
from statistics import mean, median

# Add skills/ to path so llm_utils is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from llm_utils import chat

SYSTEM_PROMPT = """\
You are a data analyst. You are given columnar statistics from a CSV dataset.
Produce a concise markdown analysis with:

## Data Insights

### Patterns
- List 2-3 notable patterns or correlations in the data

### Observations
- List 2-3 key observations about the distributions or values

### Recommendations
- Suggest 1-2 follow-up analyses or things to investigate

Be concise and data-driven. Only mention what the statistics support.
End with: *Analysis by SkillScale csv-analyzer (LLM-powered)*
"""


def infer_type(values: list[str]) -> str:
    """Infer column type from sample values."""
    numeric_count = 0
    for v in values[:100]:
        v = v.strip()
        if not v:
            continue
        try:
            float(v.replace(",", ""))
            numeric_count += 1
        except ValueError:
            pass
    non_empty = [v for v in values[:100] if v.strip()]
    if non_empty and numeric_count > len(non_empty) * 0.8:
        return "numeric"
    return "text"


def analyze_column(name: str, values: list[str]) -> dict:
    """Compute statistics for a single column."""
    non_empty = [v.strip() for v in values if v.strip()]
    col_type = infer_type(non_empty)

    stats = {
        "name": name,
        "type": col_type,
        "count": len(non_empty),
        "empty": len(values) - len(non_empty),
    }

    if col_type == "numeric":
        nums = []
        for v in non_empty:
            try:
                nums.append(float(v.replace(",", "")))
            except ValueError:
                pass
        if nums:
            stats["min"] = f"{min(nums):.2f}"
            stats["max"] = f"{max(nums):.2f}"
            stats["mean"] = f"{mean(nums):.2f}"
            stats["median"] = f"{median(nums):.2f}"
        else:
            stats["min"] = stats["max"] = stats["mean"] = stats["median"] = "N/A"
    else:
        counter = Counter(non_empty)
        stats["unique"] = len(counter)
        if counter:
            most_common = counter.most_common(1)[0]
            stats["most_common"] = f"{most_common[0]} ({most_common[1]}x)"
        else:
            stats["most_common"] = "N/A"

    return stats


def format_stats_markdown(results: list[dict], num_rows: int, num_cols: int) -> str:
    """Format column stats as markdown tables."""
    lines = [f"## CSV Analysis\n"]
    lines.append(f"**Rows:** {num_rows} | **Columns:** {num_cols}\n")

    numeric_cols = [r for r in results if r["type"] == "numeric"]
    if numeric_cols:
        lines.append("### Numeric Columns\n")
        lines.append("| Column | Count | Min | Max | Mean | Median |")
        lines.append("|--------|-------|-----|-----|------|--------|")
        for r in numeric_cols:
            lines.append(f"| {r['name']} | {r['count']} | {r['min']} | "
                         f"{r['max']} | {r['mean']} | {r['median']} |")
        lines.append("")

    text_cols = [r for r in results if r["type"] != "numeric"]
    if text_cols:
        lines.append("### Text Columns\n")
        lines.append("| Column | Type | Count | Unique | Most Common |")
        lines.append("|--------|------|-------|--------|-------------|")
        for r in text_cols:
            lines.append(f"| {r['name']} | {r['type']} | {r['count']} | "
                         f"{r.get('unique', 'N/A')} | {r.get('most_common', 'N/A')} |")
        lines.append("")

    return "\n".join(lines)


def extract_csv_data(text: str) -> str:
    """Extract CSV data from text that may contain natural language prefix.

    Tries multiple strategies:
    1. Parse straight as CSV — if it yields 2+ columns, use it.
    2. Look for a fenced code block.
    3. Find the first line that looks like a CSV header (contains commas)
       and return everything from there.
    4. Drop lines one at a time from the top until we get valid multi-column CSV.
    """
    import re

    text = text.strip()
    if not text:
        return text

    def _csv_cols(candidate: str) -> int:
        """Return the number of columns in the first row of the candidate."""
        try:
            rows = list(csv.reader(io.StringIO(candidate)))
            return len(rows[0]) if rows else 0
        except Exception:
            return 0

    # Strategy 1: whole text is valid multi-column CSV
    if _csv_cols(text) >= 2:
        return text

    # Strategy 2: fenced code blocks
    fenced = re.findall(r"```(?:csv)?\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        combined = "\n".join(f.strip() for f in fenced)
        if _csv_cols(combined) >= 2:
            return combined

    # Strategy 3: find first line with commas (likely CSV header)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "," in line:
            candidate = "\n".join(lines[i:])
            if _csv_cols(candidate) >= 2:
                return candidate

    # Give up — return original and let caller handle it
    return text


def main():
    data = os.environ.get("SKILLSCALE_INTENT", "")
    if not data:
        data = sys.stdin.read()

    if not data.strip():
        print("**Error:** No CSV data provided.", file=sys.stderr)
        sys.exit(1)

    # Extract CSV data from potentially mixed natural language + CSV input
    data = extract_csv_data(data)

    reader = csv.reader(io.StringIO(data))
    rows = list(reader)

    if len(rows) < 2:
        print("**Error:** CSV must have at least a header and one data row.",
              file=sys.stderr)
        sys.exit(1)

    headers = rows[0]
    data_rows = rows[1:]

    # Transpose: column-wise access
    columns = {h: [] for h in headers}
    for row in data_rows:
        for i, h in enumerate(headers):
            columns[h].append(row[i] if i < len(row) else "")

    # Analyze each column
    results = [analyze_column(h, columns[h]) for h in headers]

    # Format basic stats
    stats_md = format_stats_markdown(results, len(data_rows), len(headers))
    print(stats_md)

    # LLM insights
    try:
        llm_input = stats_md
        # Also include first few rows for context
        sample_rows = "\n".join(
            ",".join(row) for row in rows[:min(6, len(rows))]
        )
        llm_input += f"\n### Sample Data (first rows)\n```\n{sample_rows}\n```"

        insights = chat(SYSTEM_PROMPT, llm_input, max_tokens=101240, temperature=0.3)
        print(insights)
    except Exception as e:
        print(f"\n---\n*LLM insights unavailable: {e}*")

    print(f"\n---\n*Analyzed {len(data_rows)} rows x {len(headers)} columns.*")


if __name__ == "__main__":
    main()
