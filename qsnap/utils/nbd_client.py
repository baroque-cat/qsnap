"""LibnbdClient — production :class:`INbdClient` over ``python3-libnbd``.

Wraps the system libnbd Python bindings (distro package
``python3-libnbd``).  The ``import nbd`` is lazy — it happens inside
:meth:`LibnbdClient.connect` — so importing this module and constructing
the client never require the package; only bitmap-mode transfers do
(design D1).  ``pyproject.toml`` runtime dependencies stay empty.

Error normalization: libnbd raises ``nbd.Error`` for expected failures.
They are converted to :class:`NbdResult` error strings whose transient
conditions contain Core's retryable patterns ("eof", "timed out",
"broken pipe", "connection refused") — see
:func:`qsnap.utils.retry.is_retryable`.
"""

from __future__ import annotations

import contextlib
import errno
import importlib.util
import logging
import os
import sys
import time
from typing import Any, cast

from qsnap.interfaces.nbd import INbdClient
from qsnap.models.results import NbdExtent, NbdResult

logger = logging.getLogger(__name__)

# Default cap for a single NBD request (design D1: honor the server
# maximum, capped at 32 MiB).
_MAX_REQUEST_CAP = 32 * 1024 * 1024

_BASE_ALLOCATION_CONTEXT = "base:allocation"

# Actionable error naming the distro package (design R4: no silent
# fallback — libnbd is the transport for all backup transfers).
# Includes multi-distro install instructions and a warning about the
# unrelated PyPI ``nbd`` package (Jupyter notebook diffing tool).
MISSING_LIBNBD_ERROR = (
    "python3-libnbd is required for NBD bitmap backups.\n"
    "Install the system package:\n"
    "  Arch Linux:   sudo pacman -S libnbd\n"
    "  Debian/Ubuntu: sudo apt install python3-libnbd\n"
    "  Fedora:        sudo dnf install libnbd\n"
    "WARNING: 'pip install nbd' installs an unrelated Jupyter notebook "
    "diffing tool, NOT the libnbd bindings. Uninstall it with "
    "'pip uninstall nbd' and install the system package instead."
)

# libnbd errno names mapped to Core's retryable error patterns.
# ENOENT on a Unix-socket connect means the server side is not (yet)
# there — semantically "connection refused".
_ERRNO_TO_RETRYABLE = {
    "ECONNREFUSED": "connection refused",
    "ENOENT": "connection refused",
    "EPIPE": "broken pipe",
    "ETIMEDOUT": "timed out",
}


def _ensure_system_site_packages() -> None:
    """Append system site-packages to ``sys.path`` when running in a venv.

    When qsnap is installed in a venv (PEP 668 compliant), ``sys.path``
    excludes ``/usr/lib/python3.x/site-packages/``, making the system
    ``libnbd`` bindings invisible to ``find_spec``.  This function
    appends standard system site-packages paths to ``sys.path`` so that
    the system ``libnbd`` bindings become discoverable.

    Detection: ``VIRTUAL_ENV`` env var is set **or**
    ``sys.prefix != sys.base_prefix``.

    Safe: venv packages take precedence (they appear earlier in
    ``sys.path``).  Only existing directories are appended.
    """
    in_venv = os.environ.get("VIRTUAL_ENV") is not None or sys.prefix != sys.base_prefix
    if not in_venv:
        return

    # Standard system site-packages paths to check.
    # Use sys.version_info to generate the correct path for the current
    # Python version (e.g. python3.11, python3.14) instead of hardcoding.
    candidate_paths: list[str] = []
    py_ver = f"python3.{sys.version_info.minor}"
    # /usr/lib/python3.x/site-packages/ (Arch Linux, Fedora)
    candidate_paths.append(f"/usr/lib/{py_ver}/site-packages")
    # /usr/local/lib/python3.x/site-packages/ (manual builds)
    candidate_paths.append(f"/usr/local/lib/{py_ver}/site-packages")
    # Also check adjacent minor versions for robustness (e.g. libnbd
    # compiled for 3.12 but running on 3.13).
    for minor in range(sys.version_info.minor - 2, sys.version_info.minor + 3):
        if minor < 0:
            continue
        v = f"python3.{minor}"
        p1 = f"/usr/lib/{v}/site-packages"
        p2 = f"/usr/local/lib/{v}/site-packages"
        if p1 not in candidate_paths:
            candidate_paths.append(p1)
        if p2 not in candidate_paths:
            candidate_paths.append(p2)
    # Debian/Ubuntu dist-packages (glob pattern — check common versions)
    import glob

    candidate_paths.extend(glob.glob("/usr/lib/python3.*/dist-packages"))
    candidate_paths.extend(glob.glob("/usr/local/lib/python3.*/dist-packages"))

    for path in candidate_paths:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)


def is_libnbd_available() -> bool:
    """Return ``True`` when the system ``python3-libnbd`` bindings are importable.

    Calls :func:`_ensure_system_site_packages` before the import attempt
    to make system bindings discoverable in venv environments.  After
    importing, verifies that the module has the required libnbd
    attributes (``nbd.Error`` and ``nbd.NBD``).  This prevents false
    positives from the unrelated PyPI ``nbd`` package (Jupyter notebook
    diffing tool) which imports as ``import nbd`` but lacks those
    attributes.

    Used by env-validation and ``DefaultFactory`` to fail fast with
    :data:`MISSING_LIBNBD_ERROR` when bitmap mode is configured but the
    package is missing or wrong (design R4 — no silent fallback).
    """
    _ensure_system_site_packages()
    if importlib.util.find_spec("nbd") is None:
        return False
    try:
        import nbd
    except ImportError:
        return False
    # Verify the module has the required libnbd attributes — the PyPI
    # ``nbd`` package (Jupyter notebook diffing tool) imports as
    # ``import nbd`` but lacks ``nbd.Error`` and ``nbd.NBD``.
    return hasattr(nbd, "Error") and hasattr(nbd, "NBD")


class LibnbdClient(INbdClient):
    """``INbdClient`` implementation using the system ``nbd`` package.

    Constructing the client performs no import and no I/O; the libnbd
    handle is created lazily in :meth:`connect`.
    """

    def __init__(self) -> None:
        self._nbd: Any | None = None
        self._nbd_mod: Any | None = None
        self._max_request_size: int = _MAX_REQUEST_CAP

    # ── INbdClient implementation ─────────────────────────────────────

    def connect(self, uri: str, export_name: str, meta_contexts: list[str]) -> NbdResult:
        """Connect to *uri* requesting exactly *meta_contexts*.

        Calls :func:`_ensure_system_site_packages` before the import
        attempt to make system-installed libnbd bindings discoverable
        when running inside a venv.  After importing ``nbd``, verifies
        that the module has ``Error`` and ``NBD`` attributes — if not,
        returns :class:`NbdResult` with an actionable error message
        indicating the wrong package is installed.  Catches
        ``AttributeError`` raised by missing attributes on the imported
        module and returns :class:`NbdResult` instead of propagating the
        exception.

        Connect-retry (design D8): up to 20 attempts with a 1-second
        sleep between failures.  A fresh ``nbd.NBD()`` handle is created
        on each attempt — a half-open handle from a failed attempt is
        never reused.
        """
        _ensure_system_site_packages()
        try:
            import nbd
        except ImportError:
            return NbdResult(success=False, payload=None, error=MISSING_LIBNBD_ERROR)
        # The system bindings ship without type information; confine the
        # untyped surface to this method by treating the module as Any
        # (pyright strict would otherwise flag every attribute access).
        nbd_any = cast(Any, nbd)

        # Verify the module has the required libnbd attributes — the
        # PyPI ``nbd`` package (Jupyter notebook diffing tool) imports
        # as ``import nbd`` but lacks ``nbd.Error`` and ``nbd.NBD``.
        if not hasattr(nbd_any, "Error") or not hasattr(nbd_any, "NBD"):
            return NbdResult(
                success=False,
                payload=None,
                error=MISSING_LIBNBD_ERROR,
            )

        # Build the URI with the export name embedded (see comment below).
        effective_uri = uri
        if export_name and uri.startswith("nbd+unix:///"):
            scheme_rest, _, query = uri.partition("?")
            if scheme_rest == "nbd+unix:///":
                effective_uri = f"nbd+unix:///{export_name}" + (f"?{query}" if query else "")

        max_attempts = 20
        last_error: str = ""
        for attempt in range(1, max_attempts + 1):
            try:
                handle = nbd_any.NBD()
                for context in meta_contexts:
                    handle.add_meta_context(context)
                if export_name:
                    handle.set_export_name(export_name)
                # An nbd+unix URI with an EMPTY path (nbd+unix:///?socket=...)
                # overrides set_export_name with the empty export name "" —
                # servers that only offer named exports (libvirt's pull-mode
                # backup server exports disks by target name, e.g. "vda")
                # then reject the connect with "no export named ''".
                # Embed the export name in the URI path instead (URI takes
                # precedence over set_export_name in libnbd).
                handle.connect_uri(effective_uri)
                server_max = int(handle.get_block_size(nbd_any.SIZE_MAXIMUM))
            except nbd_any.Error as exc:
                last_error = self._normalize_error(exc)
                logger.debug(
                    "NBD connect attempt %d/%d failed: %s",
                    attempt,
                    max_attempts,
                    last_error,
                )
                # Best-effort cleanup of the failed handle.
                with contextlib.suppress(Exception):
                    handle.shutdown()
                if attempt < max_attempts:
                    time.sleep(1)
                continue
            except AttributeError:
                # The PyPI ``nbd`` imposter was installed instead of
                # system ``python3-libnbd`` — the module lacks
                # ``nbd.NBD()`` or ``nbd.Error``.  Return an actionable
                # error instead of crashing.
                return NbdResult(
                    success=False,
                    payload=None,
                    error=MISSING_LIBNBD_ERROR,
                )
            except OSError as exc:
                last_error = self._normalize_error(exc)
                logger.debug(
                    "NBD connect attempt %d/%d failed: %s",
                    attempt,
                    max_attempts,
                    last_error,
                )
                with contextlib.suppress(Exception):
                    handle.shutdown()
                if attempt < max_attempts:
                    time.sleep(1)
                continue

            self._nbd = handle
            self._nbd_mod = nbd_any
            if server_max > 0:
                self._max_request_size = min(server_max, _MAX_REQUEST_CAP)
            return NbdResult(success=True, payload=None, error=None)

        return NbdResult(success=False, payload=None, error=last_error)

    def get_size(self) -> int:
        """Return the export size in bytes (0 when not connected)."""
        if self._nbd is None:
            return 0
        return int(self._nbd.get_size())

    def get_max_request_size(self) -> int:
        """Return the maximum single-request size in bytes."""
        return self._max_request_size

    def block_status(self, offset: int, length: int) -> NbdResult:
        """Query block status; payload maps meta-context → extents.

        One NBD ``block_status`` query reports every negotiated
        meta-context; the callback fires once per context (and possibly
        in multiple chunks), so extents are accumulated per context with
        a running position starting at *offset*.
        """
        if self._nbd is None or self._nbd_mod is None:
            return NbdResult(success=False, payload=None, error="not connected")

        collected: dict[str, list[NbdExtent]] = {}
        positions: dict[str, int] = {}

        def _callback(
            metacontext: str, _chunk_offset: int, entries: list[int], _status: int
        ) -> None:
            pos = positions.get(metacontext, offset)
            extents = collected.setdefault(metacontext, [])
            for i in range(0, len(entries), 2):
                extent_length = int(entries[i])
                block_type = int(entries[i + 1])
                extents.append(
                    NbdExtent(
                        offset=pos,
                        length=extent_length,
                        data=self._extent_data_flag(metacontext, block_type),
                    )
                )
                pos += extent_length
            positions[metacontext] = pos

        try:
            self._nbd.block_status(length, offset, _callback)
        except self._nbd_mod.Error as exc:
            return NbdResult(success=False, payload=None, error=self._normalize_error(exc))
        return NbdResult(success=True, payload=collected, error=None)

    def pread(self, offset: int, length: int) -> NbdResult:
        """Read *length* bytes at *offset*, chunked to the max request size."""
        if self._nbd is None or self._nbd_mod is None:
            return NbdResult(success=False, payload=None, error="not connected")

        try:
            parts: list[bytes] = []
            pos = offset
            remaining = length
            while remaining > 0:
                chunk = min(remaining, self._max_request_size)
                parts.append(bytes(self._nbd.pread(chunk, pos)))
                pos += chunk
                remaining -= chunk
        except self._nbd_mod.Error as exc:
            return NbdResult(success=False, payload=None, error=self._normalize_error(exc))
        return NbdResult(success=True, payload=b"".join(parts), error=None)

    def pwrite(self, offset: int, data: bytes) -> NbdResult:
        """Write *data* at *offset*, chunked to the max request size."""
        if self._nbd is None or self._nbd_mod is None:
            return NbdResult(success=False, payload=None, error="not connected")

        try:
            pos = offset
            remaining = len(data)
            while remaining > 0:
                chunk = min(remaining, self._max_request_size)
                self._nbd.pwrite(data[pos - offset : pos - offset + chunk], pos)
                pos += chunk
                remaining -= chunk
        except self._nbd_mod.Error as exc:
            return NbdResult(success=False, payload=None, error=self._normalize_error(exc))
        return NbdResult(success=True, payload=None, error=None)

    def can_flush(self) -> bool:
        """Return ``True`` when the server supports ``flush()``.

        When not connected, returns ``False`` (caller should not call
        ``flush()`` on an unconnected handle).
        """
        if self._nbd is None or self._nbd_mod is None:
            return False
        try:
            return bool(self._nbd.can_flush())
        except Exception:  # noqa: BLE001 — server may not support the query
            return False

    def flush(self) -> NbdResult:
        """Flush pending writes to stable storage."""
        if self._nbd is None or self._nbd_mod is None:
            return NbdResult(success=False, payload=None, error="not connected")
        try:
            self._nbd.flush()
        except self._nbd_mod.Error as exc:
            return NbdResult(success=False, payload=None, error=self._normalize_error(exc))
        return NbdResult(success=True, payload=None, error=None)

    def disconnect(self) -> None:
        """Shut down the connection.  Safe to call when unconnected."""
        if self._nbd is None:
            return
        try:
            self._nbd.shutdown()
        except Exception as exc:  # noqa: BLE001 — shutdown is best-effort
            logger.debug("NBD shutdown raised (ignored): %s", exc)
        finally:
            self._nbd = None
            self._nbd_mod = None

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _extent_data_flag(metacontext: str, block_type: int) -> bool:
        """Map an NBD block type to the ``NbdExtent.data`` flag.

        Ported from virtnbdbackup's ``setBlockType``: for
        ``base:allocation``, 0 (allocated) and 2 (zero) carry data,
        1 (hole) and 3 (hole|zero) do not.  For dirty-bitmap contexts,
        0 is clean and 1 is dirty.
        """
        if metacontext == _BASE_ALLOCATION_CONTEXT:
            return block_type in (0, 2)
        return bool(block_type)

    @staticmethod
    def _normalize_error(exc: BaseException) -> str:
        """Normalize a libnbd error to a retry-classifiable string.

        Known transient errno names map to Core's retryable patterns;
        messages already containing a retryable pattern (or an EOF
        phrasing) keep/contain it.  All other errors pass through
        unchanged.

        ``nbd.Error.errno`` is a string name (e.g. ``"ENOENT"``) while
        ``OSError.errno`` is an integer — the latter is translated via
        :data:`errno.errorcode` before the lookup.
        """
        errno_raw = getattr(exc, "errno", None)
        errno_name: str
        if isinstance(errno_raw, int):
            errno_name = errno.errorcode.get(errno_raw, "")
        else:
            errno_name = str(errno_raw or "")
        detail = str(exc)
        lower = detail.lower()

        canonical = _ERRNO_TO_RETRYABLE.get(errno_name)
        if canonical is not None:
            if canonical in lower:
                return detail
            return f"{canonical}: {detail}"
        if ("end of file" in lower or "closed the connection" in lower) and "eof" not in lower:
            return f"eof: {detail}"
        return detail
