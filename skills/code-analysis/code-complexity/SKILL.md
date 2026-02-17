---
name: code-complexity
description: Analyzes Python source code complexity using AST metrics and LLM-powered review. Computes cyclomatic complexity, nesting depth, and function length, then provides intelligent refactoring suggestions via LLM.
---

# Code Complexity Analyzer Skill

## Purpose
Perform static analysis of Python source code to compute complexity
metrics. Uses Python's `ast` module for reliable parsing.

## Invocation
Pass Python source code via `SKILLSCALE_INTENT` environment variable
or via stdin.

## Metrics Computed
1. **Cyclomatic Complexity** — counts decision points (if/elif/for/while/and/or/except)
2. **Function Length** — lines of code per function/method
3. **Max Nesting Depth** — deepest nesting level per function
4. **Import Count** — number of imports and from-imports
5. **Class Count** — number of classes defined

## Output
Markdown-formatted report with per-function metrics table and
overall file-level summary.

## Limitations
- Python source only (not other languages).
- Does not follow imports to analyze dependencies.
- Maximum input: 500KB.
