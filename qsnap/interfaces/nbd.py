"""INbdClient — abstract NBD client interface (sibling of IShell).

All NBD protocol operations (meta-context negotiation, block-status
queries, reads, writes) go through this interface.  Production injects
:class:`qsnap.utils.nbd_client.LibnbdClient` (system ``python3-libnbd``
package); tests inject ``MockNbdClient``.  Unit tests therefore never
open a real NBD connection (TESTING.md parity with ``IShell``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from qsnap.models.results import NbdResult


class INbdClient(ABC):
    """Abstract NBD client for dirty-block transfer.

    Result objects, never exceptions for expected failures: every
    fallible method returns :class:`NbdResult` with ``success`` /
    ``payload`` / ``error``.  Expected failures (connection refused,
    server error, EOF mid-transfer, missing ``python3-libnbd`` package)
    are reported via ``error`` — they never propagate as exceptions.
    Error strings are normalized so transient conditions map to the
    existing retryable patterns ("eof", "timed out", "broken pipe",
    "connection refused"), keeping Core retry classification unchanged.
    """

    @abstractmethod
    def connect(self, uri: str, export_name: str, meta_contexts: list[str]) -> NbdResult:
        """Connect to the NBD server at *uri* and negotiate contexts.

        *uri* is an NBD URI (e.g. ``nbd:unix:/tmp/qsnap-backup-1.sock``).
        *export_name* selects the server export (e.g. ``"vda"``); empty
        string selects the default export.  *meta_contexts* lists the
        exact meta-contexts to request (e.g. ``["base:allocation",
        "qemu:dirty-bitmap:backup-vda"]``) — the client requests exactly
        these, no more, no fewer.

        Returns a failure :class:`NbdResult` when the server is
        unreachable, refuses the connection, or the ``python3-libnbd``
        package is missing (actionable error naming the package).
        """
        ...

    @abstractmethod
    def get_size(self) -> int:
        """Return the export size in bytes.  Valid after connect."""
        ...

    @abstractmethod
    def get_max_request_size(self) -> int:
        """Return the maximum single-request size in bytes.

        Honors the server-advertised maximum, capped at 32 MiB.
        Reads/writes larger than this are split into sequential chunks
        by the client.
        """
        ...

    @abstractmethod
    def block_status(self, offset: int, length: int) -> NbdResult:
        """Query block status over ``[offset, offset + length)``.

        On success, ``payload`` is a ``dict[str, list[NbdExtent]]``
        mapping each negotiated meta-context name to its extent list
        covering the requested window (one NBD block-status query
        reports every negotiated context).  The ``data`` flag carries
        the per-context semantics: allocated vs. hole/zero for
        ``base:allocation``, dirty vs. clean for
        ``qemu:dirty-bitmap:<name>``.
        """
        ...

    @abstractmethod
    def pread(self, offset: int, length: int) -> NbdResult:
        """Read *length* bytes at *offset*.

        On success, ``payload`` is ``bytes`` of exactly *length* bytes.
        Reads larger than the maximum request size are chunked
        internally and concatenated in offset order.
        """
        ...

    @abstractmethod
    def pwrite(self, offset: int, data: bytes) -> NbdResult:
        """Write *data* at *offset*.

        Writes larger than the maximum request size are chunked
        internally.  ``payload`` is ``None`` on success.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the server.  Safe to call when unconnected."""
        ...
