#!/usr/bin/env python3
"""
CSV Analyzer — Statistical summary of CSV data using only stdlib.
Reads from SKILLSCALE_INTENT env var or stdin.
Outputs markdown-formatted statistics to stdout.
"""

import csv
import io
import os
import re
import sys
from collections import Counter
from statistics import mean, median


def infer_type(values: list[str]) -> str:
    """Infer column type from sample values."""
    numeric_count = 0
    date_pattern = re.compile(r'^\d{4}[-/]\d{2}[-/]\d{2}')

    for v in values[:100]:  # sample first 100
        v = v.strip()
        if not v:
            continue
        try:
            float(v.replace(",", ""))
            numeric_count += 1
        except ValueError:
            if date_pattern.match(v):
                return "date"

    if numeric_count > len([v for v in values[:100] if v.strip()]) * 0.8:
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
            stats["most_common"] = f"{most_common[0]} ({most_common[1]}×)"
        else:
            stats["most_common"] = "N/A"

    return stats


def main():
    data = os.environ.get("SKILLSCALE_INTENT", "")
    if not data:
        data = sys.stdin.read()

    if not data.strip():
        print("**Error:** No CSV data provided.", file=sys.stderr)
        sys.exit(1)

    reader = csv.reader(io.StringIO(data))
    rows = list(reader)

    if len(rows) < 2:
        print("**Error:** CSV must have at least a header and one data row.",
              file=sys.stderr)
        sys.exit(1)

    if len(rows) > 50_001:
        print("**Error:** CSV exceeds 50,000 row limit.", file=sys.stderr)
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

    # Output markdown
    print(f"## CSV Analysis\n")
    print(f"**Rows:** {len(data_rows)} | **Columns:** {len(headers)}\n")

    # Numeric columns table
    numeric_cols = [r for r in results if r["type"] == "numeric"]
    if numeric_cols:
        print("### Numeric Columns\n")
        print("| Column | Count | Min | Max | Mean | Median |")
        print("|--------|-------|-----|-----|------|--------|")
        for r in numeric_cols:
            print(f"| {r['name']} | {r['count']} | {r['min']} | "
                  f"{r['max']} | {r['mean']} | {r['median']} |")
        print()

    # Text columns table
    text_cols = [r for r in results if r["type"] != "numeric"]
    if text_cols:
        print("### Text/Date Columns\n")
        print("| Column | Type | Count | Unique | Most Common |")
        print("|--------|------|-------|--------|-------------|")
        for r in text_cols:
            print(f"| {r['name']} | {r['type']} | {r['count']} | "
                  f"{r.get('unique', 'N/A')} | {r.get('most_common', 'N/A')} |")
        print()

    print(f"---\n*Analyzed {len(data_rows)} rows × {len(headers)} columns.*")


if __name__ == "__main__":
    main()
