#!/usr/bin/env python3
"""
Dead Code Detector — Finds unused imports, unused variables,
unreachable code, and empty functions in Python source using ast.

Reads Python source from SKILLSCALE_INTENT env var or stdin.
Outputs a markdown report to stdout.
"""

import ast
import os
import sys
from dataclasses import dataclass, field
from typing import List, Set


@dataclass
class Issue:
    kind: str       # "unused-import", "unused-variable", "unreachable", "empty-function"
    line: int
    name: str
    detail: str


def _collect_names_used(tree: ast.AST, defined_imports: Set[str]) -> Set[str]:
    """Walk the AST and collect all Name nodes that reference identifiers."""
    used: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in defined_imports:
            # This is a name being *used* somewhere
            pass
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            # e.g. os.path  — collect the root "os"
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                used.add(root.id)
    return used


def find_unused_imports(tree: ast.AST) -> List[Issue]:
    """Detect imports that are never referenced elsewhere in the code."""
    imports: dict[str, int] = {}  # name -> lineno

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imports[local] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                imports[local] = node.lineno

    # Collect all Name references (excluding the import statements themselves)
    all_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            all_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                all_names.add(root.id)

    issues = []
    for name, lineno in sorted(imports.items(), key=lambda x: x[1]):
        # An import is "used" if its name appears in a non-import context
        # Simple heuristic: count how many times it appears; if only once
        # (the import itself), it's unused.
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name:
                count += 1
            elif isinstance(node, ast.Attribute):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id == name:
                    count += 1

        # The import statement itself generates at least 0 Name references
        # (imports don't create Name nodes in the AST), so if count == 0
        # it's definitively unused
        if count == 0:
            issues.append(Issue(
                kind="unused-import",
                line=lineno,
                name=name,
                detail=f"`{name}` is imported but never used",
            ))
    return issues


def find_unused_variables(tree: ast.AST) -> List[Issue]:
    """Detect local variables assigned but never read (function scope)."""
    issues = []

    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Collect assignments (simple Name targets only)
        assigned: dict[str, int] = {}
        for node in ast.walk(func_node):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        assigned[target.id] = node.lineno
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if not node.target.id.startswith("_"):
                    assigned[node.target.id] = node.lineno

        # Collect reads (Name nodes in Load context)
        read: Set[str] = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                read.add(node.id)

        # Exclude function args
        arg_names = {a.arg for a in func_node.args.args}
        if func_node.args.vararg:
            arg_names.add(func_node.args.vararg.arg)
        if func_node.args.kwarg:
            arg_names.add(func_node.args.kwarg.arg)
        for a in func_node.args.kwonlyargs:
            arg_names.add(a.arg)

        for name, lineno in sorted(assigned.items(), key=lambda x: x[1]):
            if name not in read and name not in arg_names:
                issues.append(Issue(
                    kind="unused-variable",
                    line=lineno,
                    name=name,
                    detail=f"Variable `{name}` in `{func_node.name}()` is assigned but never read",
                ))
    return issues


def find_unreachable_code(tree: ast.AST) -> List[Issue]:
    """Detect statements after return/raise/break/continue."""
    issues = []
    TERMINAL = (ast.Return, ast.Raise, ast.Break, ast.Continue)

    for node in ast.walk(tree):
        # Look at bodies of functions, if/else, for, while, try, with
        for attr in ("body", "orelse", "finalbody", "handlers"):
            stmts = getattr(node, attr, None)
            if not isinstance(stmts, list):
                continue

            found_terminal = False
            for stmt in stmts:
                if found_terminal:
                    issues.append(Issue(
                        kind="unreachable",
                        line=stmt.lineno,
                        name=type(stmt).__name__,
                        detail=f"Code at line {stmt.lineno} is unreachable "
                               f"(after {type(prev_terminal).__name__.lower()})",
                    ))
                if isinstance(stmt, TERMINAL):
                    found_terminal = True
                    prev_terminal = stmt
    return issues


def find_empty_functions(tree: ast.AST) -> List[Issue]:
    """Detect functions whose body is only pass or Ellipsis."""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        body = node.body
        # Filter out docstrings
        real_stmts = []
        for i, stmt in enumerate(body):
            if i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Constant,)):
                if isinstance(stmt.value.value, str):
                    continue  # skip docstring
            real_stmts.append(stmt)

        is_empty = True
        for stmt in real_stmts:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is ...:
                    continue
            is_empty = False
            break

        if is_empty and real_stmts:
            issues.append(Issue(
                kind="empty-function",
                line=node.lineno,
                name=node.name,
                detail=f"Function `{node.name}()` has an empty body (only pass/...)",
            ))
    return issues


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

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"## Analysis Failed\n\n**Syntax error** at line {e.lineno}: {e.msg}")
        sys.exit(1)

    issues: List[Issue] = []
    issues.extend(find_unused_imports(tree))
    issues.extend(find_unused_variables(tree))
    issues.extend(find_unreachable_code(tree))
    issues.extend(find_empty_functions(tree))

    issues.sort(key=lambda i: i.line)

    total_lines = len(source.splitlines())

    print("## Dead Code Report\n")
    print(f"**Lines analyzed:** {total_lines} | **Issues found:** {len(issues)}\n")

    if not issues:
        print("✅ No dead code detected.\n")
    else:
        # Summary by kind
        from collections import Counter
        counts = Counter(i.kind for i in issues)
        summary_parts = []
        kind_labels = {
            "unused-import": "unused imports",
            "unused-variable": "unused variables",
            "unreachable": "unreachable statements",
            "empty-function": "empty functions",
        }
        for kind, label in kind_labels.items():
            if counts.get(kind):
                summary_parts.append(f"{counts[kind]} {label}")
        print(f"**Summary:** {', '.join(summary_parts)}\n")

        # Severity icons
        KIND_ICON = {
            "unused-import": "⚠️",
            "unused-variable": "⚠️",
            "unreachable": "🔴",
            "empty-function": "💤",
        }

        print("### Issues\n")
        print("| # | Line | Type | Description |")
        print("|---|------|------|-------------|")
        for i, issue in enumerate(issues, 1):
            icon = KIND_ICON.get(issue.kind, "❓")
            print(f"| {i} | {issue.line} | {icon} {issue.kind} | {issue.detail} |")
        print()

    print(f"---\n*Scanned {total_lines} lines of Python source for dead code.*")


if __name__ == "__main__":
    main()
