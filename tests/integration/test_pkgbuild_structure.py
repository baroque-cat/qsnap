"""Integration tests for PKGBUILD structural correctness.

These tests do NOT run makepkg.  They parse the PKGBUILD as text and
verify it satisfies the structural requirements for system Python
installation and libnbd discoverability.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PKGBUILD = PROJECT_ROOT / "PKGBUILD"


@pytest.mark.integration
def test_pkgbuild_libnbd_on_syspath() -> None:
    """Verify PKGBUILD structure allows libnbd import after installation.

    Three checks:

    1. The ``depends`` array includes ``libnbd`` so the system
       libnbd bindings are installed alongside qsnap.

    2. The package step uses ``pip install --prefix="/usr"`` so
       qsnap is installed to the system Python site-packages,
       NOT a virtual environment.  The system libnbd bindings
       are only discoverable from the system Python interpreter.

    3. The expected install path (``/usr/lib/python3.X/site-packages/``)
       is on the current system Python's ``sys.path``, confirming
       that ``import qsnap`` would work after installation.
    """
    content = PKGBUILD.read_text()

    # ── Check 1: depends includes libnbd ─────────────────────────────
    depends_match = re.search(
        r"depends=\(\s*('([^']*)'\s*)+\)", content
    )
    assert depends_match is not None, (
        "PKGBUILD missing 'depends' array"
    )

    # Extract all quoted strings from the depends array.
    depends_entries = re.findall(r"'([^']*)'", depends_match.group(0))
    assert "libnbd" in depends_entries, (
        f"PKGBUILD 'depends' array missing 'libnbd'.  "
        f"Found: {depends_entries}"
    )

    # ── Check 2: pip install targets system Python (prefix=/usr) ────
    pip_line = None
    for line in content.splitlines():
        if "pip install" in line and "--prefix=" in line:
            pip_line = line.strip()
            break

    assert pip_line is not None, (
        "PKGBUILD missing 'pip install --prefix=...' line"
    )
    assert "--prefix=" in pip_line and "/usr" in pip_line, (
        f"PKGBUILD pip install does not target system Python prefix /usr.  "
        f"Line: {pip_line}"
    )

    # ── Check 3: system Python sys.path contains expected install dir ─
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    expected_site_packages = f"/usr/lib/python{py_ver}/site-packages"

    # Normalize paths for comparison (handle trailing slashes, etc.).
    normalized_sys_path = [str(Path(p)) for p in sys.path]

    assert expected_site_packages in normalized_sys_path, (
        f"System Python sys.path does not contain {expected_site_packages!r}.  "
        f"pip install --prefix=/usr would install qsnap to this directory, "
        f"but it is not on sys.path — import qsnap would fail.  "
        f"sys.path: {sys.path}"
    )
