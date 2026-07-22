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

import errno
import importlib.util
import logging
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
MISSING_LIBNBD_ERROR = (
    "python3-libnbd is required for NBD bitmap backups — install via: apt install python3-libnbd"
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


def is_libnbd_available() -> bool:
    """Return ``True`` when the system ``python3-libnbd`` bindings are importable.

    Uses :func:`importlib.util.find_spec` so the check performs no import
    side effects.  Used by env-validation and ``DefaultFactory`` to fail
    fast with :data:`MISSING_LIBNBD_ERROR` when bitmap mode is configured
    but the package is missing (design R4 — no silent fallback).
    """
    return importlib.util.find_spec("nbd") is not None


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

        Lazy-imports the ``nbd`` package; when it is missing, returns an
        actionable error naming ``python3-libnbd``.  After connecting,
        reads the server-advertised maximum request size and applies the
        32 MiB cap.
        """
        try:
            import nbd
        except ImportError:
            return NbdResult(success=False, payload=None, error=MISSING_LIBNBD_ERROR)
        # The system bindings ship without type information; confine the
        # untyped surface to this method by treating the module as Any
        # (pyright strict would otherwise flag every attribute access).
        nbd_any = cast(Any, nbd)

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
            if export_name and uri.startswith("nbd+unix:///"):
                scheme_rest, _, query = uri.partition("?")
                if scheme_rest == "nbd+unix:///":
                    uri = f"nbd+unix:///{export_name}" + (f"?{query}" if query else "")
            handle.connect_uri(uri)
            server_max = int(handle.get_block_size(nbd_any.SIZE_MAXIMUM))
        except nbd_any.Error as exc:
            return NbdResult(success=False, payload=None, error=self._normalize_error(exc))
        except OSError as exc:
            return NbdResult(success=False, payload=None, error=self._normalize_error(exc))

        self._nbd = handle
        self._nbd_mod = nbd_any
        if server_max > 0:
            self._max_request_size = min(server_max, _MAX_REQUEST_CAP)
        return NbdResult(success=True, payload=None, error=None)

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
