"""Integration tests for NBD import hardening.

Covers the ``LibnbdClient.connect()`` hardening against missing or
imposter ``nbd`` modules:

- ``import nbd`` raises ``ImportError`` → ``connect()`` returns
  ``NbdResult(success=False)`` with an actionable error message.
- PyPI ``nbd`` imposter (module without ``Error`` / ``NBD`` attributes)
  → ``connect()`` returns ``NbdResult(success=False)`` instead of
  crashing with ``AttributeError``.

All tests are marked ``@pytest.mark.integration``.  They use a real
``LibnbdClient`` instance but simulate the missing/imposter scenarios
via ``unittest.mock``.  When real libnbd is installed, the test also
verifies the imposter guarding via a fake ``sys.modules`` entry.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_nbd_import_hardening.py -v -m integration
"""

from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest

from qsnap.models.results import NbdResult
from qsnap.utils.nbd_client import LibnbdClient, MISSING_LIBNBD_ERROR


# ──────────────────────────────────────────────────────────────────────
# Test 1: connect() returns NbdResult when nbd module is missing
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_nbd_connect_no_crash_on_missing_module():
    """Verify ``LibnbdClient.connect()`` returns ``NbdResult(success=False)``
    instead of crashing when ``import nbd`` raises ``ImportError``.

    This test creates a real ``LibnbdClient`` and simulates the missing
    ``nbd`` module by patching ``builtins.__import__`` to raise
    ``ImportError`` for the ``nbd`` package.  All other imports are
    passed through unchanged.

    Asserts:
    - ``connect()`` does NOT raise an exception.
    - The returned ``NbdResult.success`` is ``False``.
    - The error message includes the ``MISSING_LIBNBD_ERROR`` text.
    """
    import builtins

    _real_import = builtins.__import__

    def _block_nbd_import(name, *args, **kwargs):
        if name == "nbd":
            raise ImportError("No module named 'nbd'")
        return _real_import(name, *args, **kwargs)

    client = LibnbdClient()

    with mock.patch("builtins.__import__", side_effect=_block_nbd_import):
        result = client.connect(
            "nbd+unix:///?socket=/nonexistent",
            "test-export",
            ["base:allocation"],
        )

    assert result is not None, "connect() should return NbdResult, not None"
    assert isinstance(result, NbdResult), (
        f"connect() should return NbdResult, got {type(result).__name__!r}"
    )
    assert result.success is False, (
        "Expected success=False when nbd module is missing, got success=True"
    )
    assert result.error is not None, "Error should be set when nbd module is missing"
    assert "python3-libnbd" in result.error, (
        f"Error message should mention python3-libnbd, got: {result.error!r}"
    )
    assert "pip install nbd" in result.error, (
        f"Error message should warn about PyPI imposter, got: {result.error!r}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 2: connect() returns NbdResult with PyPI nbd imposter
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_nbd_connect_pypi_imposter_returns_actionable_error():
    """Verify ``LibnbdClient.connect()`` returns ``NbdResult(success=False)``
    when the PyPI ``nbd`` imposter is installed (module imports but
    lacks ``Error`` and ``NBD`` attributes).

    This test installs a fake ``nbd`` module in ``sys.modules`` that
    does NOT have ``Error`` or ``NBD`` attributes.  The real libnbd
    module is removed from ``sys.modules`` during the test.

    After the attribute verification in ``connect()`` fails, the method
    must return ``NbdResult`` — it must NOT crash with ``AttributeError``.

    Asserts:
    - ``connect()`` returns ``NbdResult(success=False)``.
    - No ``AttributeError`` is raised.
    - The error message includes the multi-distro instructions.
    """
    # First, check if this test is even meaningful.  If libnbd is not
    # installed, then import nbd would fail and we'd be testing the
    # ImportError path (already covered by test 1).  Skip to avoid
    # double-coverage noise.
    try:
        import nbd  # noqa: F401
    except ImportError:
        pytest.skip("python3-libnbd not installed — exercise ImportError path instead")

    # Create a fake "nbd" module that lacks Error and NBD attributes.
    fake_nbd = importlib.import_module("types").ModuleType("nbd")
    # (intentionally empty — no Error, no NBD)

    # Store real nbd module reference so we can restore it.
    real_nbd = sys.modules.get("nbd")

    client = LibnbdClient()

    try:
        # Replace sys.modules['nbd'] with the fake imposter.
        sys.modules["nbd"] = fake_nbd

        result = client.connect(
            "nbd+unix:///?socket=/nonexistent",
            "test-export",
            ["base:allocation"],
        )
    finally:
        # Restore the real nbd module (or None if it wasn't there).
        if real_nbd is not None:
            sys.modules["nbd"] = real_nbd
        elif "nbd" in sys.modules:
            del sys.modules["nbd"]

    assert result is not None, "connect() should return NbdResult, not None"
    assert result.success is False, (
        f"Expected success=False with PyPI imposter, got success={result.success!r}"
    )
    assert result.error is not None, "Error should be set with PyPI imposter"
    assert "python3-libnbd" in result.error, (
        f"Error message should mention python3-libnbd, got: {result.error!r}"
    )
    # The MISSING_LIBNBD_ERROR includes the PyPI warning.
    assert "pip install nbd" in result.error, (
        f"Error must warn about PyPI pip nbd, got: {result.error!r}"
    )


# ──────────────────────────────────────────────────────────────────────
# Test 3: MISSING_LIBNBD_ERROR includes multi-distro instructions
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_missing_libnbd_error_includes_arch_instructions():
    """Verify ``MISSING_LIBNBD_ERROR`` includes Arch Linux install
    instructions (``pacman -S libnbd``) and warns about the PyPI
    imposter.

    This is a structural check on the constant itself — ensures the
    multi-distro message format is present regardless of whether libnbd
    is actually installed.
    """
    assert "Arch Linux" in MISSING_LIBNBD_ERROR, (
        "MISSING_LIBNBD_ERROR should include Arch Linux instructions"
    )
    assert "pacman -S libnbd" in MISSING_LIBNBD_ERROR, (
        "MISSING_LIBNBD_ERROR should include pacman install command"
    )
    assert "Debian/Ubuntu" in MISSING_LIBNBD_ERROR or "apt" in MISSING_LIBNBD_ERROR, (
        "MISSING_LIBNBD_ERROR should include Debian/Ubuntu instructions"
    )
    assert "Fedora" in MISSING_LIBNBD_ERROR or "dnf" in MISSING_LIBNBD_ERROR, (
        "MISSING_LIBNBD_ERROR should include Fedora instructions"
    )
    assert "pip install nbd" in MISSING_LIBNBD_ERROR or "pip uninstall nbd" in MISSING_LIBNBD_ERROR, (
        "MISSING_LIBNBD_ERROR should warn about the PyPI nbd imposter"
    )
