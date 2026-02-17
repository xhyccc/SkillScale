#!/usr/bin/env python3
"""
Code Complexity Analyzer — AST metrics + LLM-powered review.
Reads Python source from SKILLSCALE_INTENT env var or stdin.
Outputs markdown metrics + LLM suggestions to stdout.
"""

import ast
import os
import sys
from dataclasses import dataclass

# Add skills/ to path so llm_utils is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
from llm_utils import chat

SYSTEM_PROMPT = """\
You are an expert Python code reviewer focused on complexity analysis.
You are given AST-computed metrics for a piece of Python code plus the
source itself. Provide a concise markdown review:

## Refactoring Suggestions

For functions with high cyclomatic complexity (CC > 5) or deep nesting:
- Explain *why* the complexity is high (specific patterns)
- Suggest *concrete* refactoring steps (extract method, guard clauses, etc.)

If all functions are simple, say so briefly and suggest any general
improvements you notice.

Keep it concise (3-8 bullet points max). End with:
*Review by SkillScale code-complexity (LLM-powered)*
"""


@dataclass
class FunctionMetrics:
    name: str
    lineno: int
    end_lineno: int
    length: int
    cyclomatic: int
    max_nesting: int
    args_count: int


DECISION_NODES = (
    ast.If, ast.IfExp, ast.For, ast.While,
    ast.ExceptHandler, ast.With, ast.Assert,
    ast.comprehension,
)


def cyclomatic_complexity(node: ast.AST) -> int:
    count = 1
    for child in ast.walk(node):
        if isinstance(child, DECISION_NODES):
            count += 1
        elif isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
    return count


def max_nesting_depth(node: ast.AST, current: int = 0) -> int:
    max_depth = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With,
                               ast.Try, ast.ExceptHandler)):
            max_depth = max(max_depth, max_nesting_depth(child, current + 1))
        else:
            max_depth = max(max_depth, max_nesting_depth(child, current))
    return max_depth


def analyze_function(node) -> FunctionMetrics:
    lineno = node.lineno
    end_lineno = getattr(node, "end_lineno", lineno)
    length = end_lineno - lineno + 1
    cc = cyclomatic_complexity(node)
    depth = max_nesting_depth(node)
    args_count = len(node.args.args)
    if node.args.vararg:
        args_count += 1
    if node.args.kwarg:
        args_count += 1
    return FunctionMetrics(
        name=node.name, lineno=lineno, end_lineno=end_lineno,
        length=length, cyclomatic=cc, max_nesting=depth,
        args_count=args_count,
    )


def complexity_rating(cc: int) -> str:
    if cc <= 5:
        return "Low"
    elif cc <= 10:
        return "Moderate"
    elif cc <= 20:
        return "High"
    else:
        return "Very High"


def main():
    source = os.environ.get("SKILLSCALE_INTENT", "")
    if not source:
        source = sys.stdin.read()

    if not source.strip():
        print("**Error:** No Python source code provided.", file=sys.stderr)
        sys.exit(1)

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"## Analysis Failed\n\nSyntax error at line {e.lineno}: {e.msg}")
        sys.exit(1)

    functions = []
    classes = []
    imports = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(analyze_function(node))
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            else:
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}")

    total_lines = len(source.splitlines())

    # Print AST metrics
    print("## Code Complexity Report\n")
    print(f"**Total Lines:** {total_lines} | "
          f"**Functions:** {len(functions)} | "
          f"**Classes:** {len(classes)} | "
          f"**Imports:** {len(imports)}\n")

    if functions:
        print("### Function Metrics\n")
        print("| Function | Lines | CC | Rating | Nesting | Args |")
        print("|----------|-------|----|--------|---------|------|")
        for f in sorted(functions, key=lambda x: x.cyclomatic, reverse=True):
            print(f"| `{f.name}` | {f.length} | {f.cyclomatic} | "
                  f"{complexity_rating(f.cyclomatic)} | {f.max_nesting} | "
                  f"{f.args_count} |")
        print()

        avg_cc = sum(f.cyclomatic for f in functions) / len(functions)
        max_cc = max(f.cyclomatic for f in functions)
        print(f"**Average Complexity:** {avg_cc:.1f} | "
              f"**Max Complexity:** {max_cc} ({complexity_rating(max_cc)})\n")

    # LLM review
    try:
        # Build metrics summary for LLM
        metrics_text = f"Total lines: {total_lines}, Functions: {len(functions)}\n"
        for f in functions:
            metrics_text += (f"- {f.name}(): CC={f.cyclomatic}, nesting={f.max_nesting}, "
                            f"lines={f.length}, args={f.args_count}\n")

        llm_input = f"### Metrics\n{metrics_text}\n### Source Code\n```python\n{source[:4000]}\n```"
        review = chat(SYSTEM_PROMPT, llm_input, max_tokens=512, temperature=0.3)
        print(review)
    except Exception as e:
        print(f"\n*LLM review unavailable: {e}*")

    print(f"\n---\n*Analyzed {total_lines} lines of Python source.*")


if __name__ == "__main__":
    main()
