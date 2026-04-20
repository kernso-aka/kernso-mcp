"""Safety invariant tests — CI enforcement of hard rules."""

import pathlib


SRC_DIR = pathlib.Path(__file__).parent.parent / "src" / "kernso_mcp"


def _read_all_py():
    return [(f.name, f.read_text()) for f in SRC_DIR.rglob("*.py")]


def test_no_print_statements():
    """Rule 3: No print() to stdout."""
    for name, content in _read_all_py():
        # Allow 'print(' in comments/strings is hard to detect perfectly,
        # but bare print( at start of line or after whitespace is the pattern
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("print(") or stripped.startswith("print ("):
                pytest.fail(f"{name}:{i} — print() found: {stripped}")


def test_no_shell_execution():
    """Rule 2: No subprocess, os.system, eval(), exec()."""
    banned = ["subprocess", "os.system", "eval(", "exec("]
    for name, content in _read_all_py():
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pattern in banned:
                if pattern in stripped:
                    pytest.fail(f"{name}:{i} — banned pattern '{pattern}': {stripped}")


def test_no_write_tools():
    """Rule 4: No write/mutate tool definitions."""
    for name, content in _read_all_py():
        for i, line in enumerate(content.splitlines(), 1):
            if "def create_" in line or "def update_" in line or "def delete_" in line:
                if "@mcp.tool" in content[max(0, content.find(line)-200):content.find(line)]:
                    pytest.fail(f"{name}:{i} — write tool found: {line.strip()}")


def test_no_hardcoded_credentials():
    """Rule 5: No hardcoded credentials."""
    sensitive = ["kernso-kg-2026", "sk-ant-", "sk-proj-"]
    for name, content in _read_all_py():
        for pattern in sensitive:
            if pattern in content:
                pytest.fail(f"{name} — hardcoded credential pattern: {pattern}")


def test_all_types_from_kernso_schemas():
    """No duplicate Pydantic model definitions."""
    for name, content in _read_all_py():
        for i, line in enumerate(content.splitlines(), 1):
            if line.strip().startswith("class ") and "(BaseModel)" in line:
                pytest.fail(f"{name}:{i} — local BaseModel class: {line.strip()}")
