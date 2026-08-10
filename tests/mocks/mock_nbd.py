"""MockNbdClient — mock INbdClient for unit tests.

Supports configurable ``block_status``/``pread``/``pwrite`` responses,
per-method failure injection, and full call-history recording
(TESTING.md: every ABC gets a mock; unit tests never open a real NBD
connection).
"""

from __future__ import annotations

from collections.abc import Callable

from qsnap.interfaces.nbd import INbdClient
from qsnap.models.results import NbdExtent, NbdResult


class MockNbdClient(INbdClient):
    """Mock NBD client with configurable responses and failure injection.

    Configuration knobs:

    - ``size`` / ``max_request_size``: returned by ``get_size()`` /
      ``get_max_request_size()``.
    - ``block_status_payload``: payload returned by every successful
      ``block_status`` call (meta-context name → ``list[NbdExtent]``).
    - ``fail_connect`` / ``fail_block_status`` / ``fail_pread`` /
      ``fail_pwrite``: when set to an error string, the method returns
      ``NbdResult(success=False, error=...)``.
    - ``block_status_handler`` / ``pread_handler`` / ``pwrite_handler``:
      optional callables that fully override default behavior (e.g. to
      sleep and trigger the in-process stall watchdog, or to fail only
      after N calls).
    - ``calls``: full call history as ``(method, *args)`` tuples.
    - ``requested_contexts``: meta-contexts passed to ``connect()``.
    - ``writes``: recorded ``(offset, data)`` tuples from ``pwrite``.
    - ``bytes_read`` / ``bytes_written``: totals for dirty-bound
      assertions.
    """

    def __init__(
        self,
        *,
        size: int = 0,
        max_request_size: int = 32 * 1024 * 1024,
    ) -> None:
        self._size = size
        self._max_request_size = max_request_size

        self.calls: list[tuple[str, object, object]] = []
        self.requested_contexts: list[str] = []
        self.connected_uri: str | None = None
        self.connected_export: str | None = None
        self.disconnect_count = 0
        self.flush_count = 0
        self.connect_attempts = 0

        self.block_status_payload: dict[str, list[NbdExtent]] = {}
        self.block_status_handler: Callable[[int, int], NbdResult] | None = None
        self.pread_handler: Callable[[int, int], NbdResult] | None = None
        self.pwrite_handler: Callable[[int, bytes], NbdResult] | None = None

        self.fail_connect: str | None = None
        self.fail_block_status: str | None = None
        self.fail_pread: str | None = None
        self.fail_pwrite: str | None = None

        self.writes: list[tuple[int, bytes]] = []
        self.bytes_read = 0
        self.bytes_written = 0

        # Per-socket payloads for endpoint-aware block_status (used by
        # recovered-delta copy-loop tests where different layers served
        # on different sockets return different allocated extents).
        self._socket_payloads: dict[str, dict[str, list[NbdExtent]]] = {}
        self._connected_socket: str | None = None

    def set_socket_payload(
        self, socket_path: str, payload: dict[str, list[NbdExtent]]
    ) -> None:
        """Set the block_status payload returned when connected to *socket_path*.

        When connected to a socket that has a registered payload,
        ``block_status`` returns that payload instead of the default
        ``block_status_payload``.  This is used in recovered-delta
        copy-loop tests where the write server returns all holes and
        each read-server layer returns its allocated extents.
        """
        self._socket_payloads[socket_path] = payload

    # ── INbdClient implementation ─────────────────────────────────────

    def connect(self, uri: str, export_name: str, meta_contexts: list[str]) -> NbdResult:
        self.calls.append(("connect", uri, export_name))
        self.requested_contexts = list(meta_contexts)
        self.connect_attempts += 1
        if self.fail_connect is not None:
            return NbdResult(success=False, payload=None, error=self.fail_connect)
        self.connected_uri = uri
        self.connected_export = export_name
        # Extract the unix socket path from the NBD URI (used by
        # per-socket payload dispatch in block_status).
        self._connected_socket = uri.replace("nbd+unix:///?socket=", "")
        return NbdResult(success=True, payload=None, error=None)

    def get_size(self) -> int:
        return self._size

    def get_max_request_size(self) -> int:
        return self._max_request_size

    def block_status(self, offset: int, length: int) -> NbdResult:
        self.calls.append(("block_status", offset, length))
        if self.block_status_handler is not None:
            return self.block_status_handler(offset, length)
        if self.fail_block_status is not None:
            return NbdResult(success=False, payload=None, error=self.fail_block_status)
        # Per-socket payload dispatch: when connected to a socket with
        # a registered payload return that; otherwise fall back to the
        # default block_status_payload (for backwards compatibility with
        # non-copy-loop tests).
        payload = self.block_status_payload
        if self._connected_socket is not None:
            sock_payload = self._socket_payloads.get(self._connected_socket)
            if sock_payload is not None:
                payload = sock_payload
        return NbdResult(success=True, payload=dict(payload), error=None)

    def pread(self, offset: int, length: int) -> NbdResult:
        self.calls.append(("pread", offset, length))
        if self.pread_handler is not None:
            return self.pread_handler(offset, length)
        if self.fail_pread is not None:
            return NbdResult(success=False, payload=None, error=self.fail_pread)
        self.bytes_read += length
        return NbdResult(success=True, payload=bytes(length), error=None)

    def pwrite(self, offset: int, data: bytes) -> NbdResult:
        self.calls.append(("pwrite", offset, len(data)))
        if self.pwrite_handler is not None:
            return self.pwrite_handler(offset, data)
        if self.fail_pwrite is not None:
            return NbdResult(success=False, payload=None, error=self.fail_pwrite)
        self.writes.append((offset, data))
        self.bytes_written += len(data)
        return NbdResult(success=True, payload=None, error=None)

    def can_flush(self) -> bool:
        self.calls.append(("can_flush", None, None))
        return True

    def flush(self) -> NbdResult:
        self.calls.append(("flush", None, None))
        self.flush_count += 1
        return NbdResult(success=True, payload=None, error=None)

    def disconnect(self) -> None:
        self.calls.append(("disconnect", None, None))
        self.disconnect_count += 1
