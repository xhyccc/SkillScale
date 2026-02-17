---
name: dead-code-detector
description: Detects dead code in Python source including unused imports, unused variables, unreachable code after return/raise/break/continue, and empty function bodies. Reports issues with line numbers.
license: MIT
compatibility: python3
allowed-tools: python3 ast
---

# Dead Code Detector Skill

## Purpose
Static analysis of Python source code to find dead code patterns:
unused imports, unused variables, unreachable statements, and empty
function bodies. Uses Python's `ast` module for parsing.

## Invocation
Pass Python source code via `SKILLSCALE_INTENT` environment variable
or via stdin.

## Checks Performed
1. **Unused Imports** — imports that are never referenced in the code
2. **Unused Variables** — local variables assigned but never read
3. **Unreachable Code** — statements after return, raise, break, continue
4. **Empty Functions** — function/method bodies that contain only `pass` or `...`

## Output
Markdown-formatted report listing each issue with its type, line
number, and description.

## Limitations
- Python source only.
- Does not resolve dynamic attribute access or `globals()`/`locals()`.
- Does not follow imports across modules.
- Maximum input: 500KB.
