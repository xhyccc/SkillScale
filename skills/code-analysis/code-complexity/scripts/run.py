#!/usr/bin/env python3
"""
Code Complexity Analyzer — Static analysis of Python source using ast.
Reads Python source from SKILLSCALE_INTENT env var or stdin.
Outputs markdown metrics to stdout.
"""

import ast
import os
import sys
from dataclasses import dataclass


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
    """Count decision points in an AST subtree."""
    count = 1  # base path
    for child in ast.walk(node):
        if isinstance(child, DECISION_NODES):
            count += 1
        elif isinstance(child, ast.BoolOp):
            # Each 'and' / 'or' adds a path
            count += len(child.values) - 1
    return count


def max_nesting_depth(node: ast.AST, current: int = 0) -> int:
    """Compute maximum nesting depth of control flow."""
    max_depth = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With,
                               ast.Try, ast.ExceptHandler)):
            max_depth = max(max_depth, max_nesting_depth(child, current + 1))
        else:
            max_depth = max(max_depth, max_nesting_depth(child, current))
    return max_depth


def analyze_function(node) -> FunctionMetrics:
    """Analyze a single function or method definition."""
    name = node.name
    if hasattr(node, "decorator_list") and node.decorator_list:
        # Include class context if method
        pass

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
        name=name,
        lineno=lineno,
        end_lineno=end_lineno,
        length=length,
        cyclomatic=cc,
        max_nesting=depth,
        args_count=args_count,
    )


def analyze_source(source: str) -> dict:
    """Full analysis of Python source code."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": f"Syntax error at line {e.lineno}: {e.msg}"}

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

    return {
        "functions": functions,
        "classes": classes,
        "imports": imports,
        "total_lines": total_lines,
    }


def complexity_rating(cc: int) -> str:
    """Human-readable complexity rating."""
    if cc <= 5:
        return "🟢 Low"
    elif cc <= 10:
        return "🟡 Moderate"
    elif cc <= 20:
        return "🟠 High"
    else:
        return "🔴 Very High"


def main():
    source = os.environ.get("SKILLSCALE_INTENT", "")
    if not source:
        source = sys.stdin.read()

    if not source.strip():
        print("**Error:** No Python source code provided.", file=sys.stderr)
        sys.exit(1)

    if len(source) > 500_000:
        print("**Error:** Source exceeds 500KB limit.", file=sys.stderr)
        sys.exit(1)

    result = analyze_source(source)

    if "error" in result:
        print(f"## Analysis Failed\n\n{result['error']}")
        sys.exit(1)

    functions = result["functions"]
    classes = result["classes"]
    imports = result["imports"]
    total_lines = result["total_lines"]

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
              f"**Max Complexity:** {max_cc} "
              f"({complexity_rating(max_cc)})\n")

    if classes:
        print(f"### Classes\n")
        for c in classes:
            print(f"- `{c}`")
        print()

    if imports:
        print(f"### Imports ({len(imports)})\n")
        for imp in sorted(imports):
            print(f"- `{imp}`")
        print()

    print(f"---\n*Analyzed {total_lines} lines of Python source.*")


if __name__ == "__main__":
    main()
