"""Verify that CLI layers contain no business-logic imports.

The CLI modules (``commands.py`` and ``app.py``) must be thin
translation layers.  They may import infrastructure (``Core``,
``ConfigFacade``, ``LockManager``, ``DefaultFactory``, etc.) and
stdlib, but must never import domain modules (``qsnap.modules``,
``qsnap.retention``, ``qsnap.state``).

These tests parse the source files with ``ast`` and walk every
``Import`` / ``ImportFrom`` node, asserting that no imported module
name starts with a forbidden prefix.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ── helpers ───────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _imported_modules(source: str) -> list[str]:
    """Return a list of all module names imported by *source*."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                names.append(node.module)
    return names


def _starts_with_any(name: str, prefixes: tuple[str, ...]) -> bool:
    """True if *name* starts with any of *prefixes* (module-boundary aware)."""
    for prefix in prefixes:
        if name == prefix or name.startswith(prefix + "."):
            return True
    return False


# ── commands.py ───────────────────────────────────────────────────────────

_FORBIDDEN_COMMANDS = ("qsnap.modules", "qsnap.config", "qsnap.retention", "qsnap.state")


def test_commands_py_has_no_business_logic_imports():
    """``commands.py`` must not import from business-logic packages."""
    source = (_PROJECT_ROOT / "qsnap" / "cli" / "commands.py").read_text()
    modules = _imported_modules(source)
    violations = [m for m in modules if _starts_with_any(m, _FORBIDDEN_COMMANDS)]
    assert not violations, (
        f"commands.py imports forbidden business-logic modules: {violations}"
    )


# ── app.py ────────────────────────────────────────────────────────────────

_FORBIDDEN_APP = ("qsnap.modules", "qsnap.retention", "qsnap.state")


def test_app_py_has_no_business_logic_imports():
    """``app.py`` may import ``qsnap.config.facade`` and ``qsnap.locking``
    but must not import ``qsnap.modules``, ``qsnap.retention``, or
    ``qsnap.state``."""
    source = (_PROJECT_ROOT / "qsnap" / "cli" / "app.py").read_text()
    modules = _imported_modules(source)
    violations = [m for m in modules if _starts_with_any(m, _FORBIDDEN_APP)]
    assert not violations, (
        f"app.py imports forbidden business-logic modules: {violations}"
    )
