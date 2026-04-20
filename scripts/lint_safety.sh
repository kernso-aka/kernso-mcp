#!/bin/bash
# CI lint check: enforces hard rules from the MCP spec
# Rules 2-5: no shell exec, no print(), no write tools, no hardcoded creds
set -e

SRC_DIR="${1:-src/kernso_mcp}"
FAIL=0

echo "=== Safety lint: $SRC_DIR ==="

# Rule 2: No subprocess, os.system, eval(), exec()
if grep -rEn "(subprocess|os\.system|eval\(|exec\()" "$SRC_DIR" --include="*.py"; then
    echo "FAIL: Rule 2 — found subprocess/os.system/eval/exec"
    FAIL=1
else
    echo "PASS: Rule 2 — no shell execution"
fi

# Rule 3: No print() to stdout
if grep -rn "print(" "$SRC_DIR" --include="*.py"; then
    echo "FAIL: Rule 3 — found print() statements"
    FAIL=1
else
    echo "PASS: Rule 3 — no print() statements"
fi

# Rule 4: No write/mutate tools (create, update, delete, cart, purchase)
if grep -rEn "def (create_|update_|delete_|add_to_cart|purchase|send_)" "$SRC_DIR" --include="*.py"; then
    echo "FAIL: Rule 4 — found write/mutate tool definitions"
    FAIL=1
else
    echo "PASS: Rule 4 — no write tools"
fi

# Rule 5: No hardcoded credentials (skip comments and docstrings)
if grep -rEn "(neo4j|password|sk-[a-zA-Z0-9]|kernso-kg-)" "$SRC_DIR" --include="*.py" | grep -v "\.example\|\.env" | grep -v "^\s*#" | grep -v '"""' | grep -v "'''" ; then
    echo "FAIL: Rule 5 — found potential hardcoded credentials"
    FAIL=1
else
    echo "PASS: Rule 5 — no hardcoded credentials"
fi

# Bonus: verify all types imported from kernso_schemas (no local class defs)
if grep -rEn "class (Product|BrandKernel|ResolveIntent|Resolution|Category|CoverageFlag)\b" "$SRC_DIR" --include="*.py" | grep -v "import"; then
    echo "FAIL: Found local class definitions that should come from kernso-schemas"
    FAIL=1
else
    echo "PASS: No duplicate schema definitions"
fi

if [ $FAIL -ne 0 ]; then
    echo "=== SAFETY LINT FAILED ==="
    exit 1
fi

echo "=== ALL SAFETY CHECKS PASSED ==="
