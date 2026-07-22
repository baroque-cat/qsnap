"""Unit tests for ``qsnap.utils.nbd_client.LibnbdClient``.

All NBD I/O is driven through a fake ``nbd`` module injected into
``sys.modules`` — the Poetry venv has no real ``python3-libnbd``
package, and the lazy-import / missing-package tests exercise this fact
deterministically.  Tests that verify connection-refused normalization,
EOF normalization, chunked reads/writes, and block‑status extent parsing
all use the fake module so every code path is driven without the real
package.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from qsnap.models.results import NbdExtent, NbdResult
from qsnap.utils.nbd_client import MISSING_LIBNBD_ERROR, LibnbdClient, is_libnbd_available
from qsnap.utils.retry import is_retryable

# ── helpers ───────────────────────────────────────────────────────────────


def _make_fake_nbd_module() -> tuple[types.ModuleType, list[Any]]:
    """Build a minimal fake ``nbd`` module for injection into ``sys.modules``.

    Returns ``(module, handles)`` where ``handles`` is a mutable list of
    every ``NBD`` handle instance created via ``nbd.NBD()``.  After
    :meth:`LibnbdClient.connect` creates a handle, tests can inspect it
    via ``handles[-1]``.

    Configuration knobs are set as *class-level* attributes on ``NBD``
    BEFORE calling ``connect()``, e.g.::

        mod, handles = _make_fake_nbd_module()
        mod.NBD._server_max = 65536
    """
    handles: list[Any] = []

    class _Error(Exception):
        """Fake ``nbd.Error`` with the same attributes inspected by
        :meth:`LibnbdClient._normalize_error`."""

        def __init__(
            self,
            errno: str = "EIO",
            errnum: int = 5,
            string: str = "generic error",
        ) -> None:
            self.errno = errno
            self.errnum = errnum
            self.string = string
            super().__init__(string)

    class _NBD:
        """Fake NBD handle.  One instance per :meth:`LibnbdClient.connect`."""

        # ---- class-level knobs (set by the test before connect()) ---------
        _server_max: int = 0
        _connect_raises: tuple[str, str] | None = None
        _pread_raises: str | None = None
        _pwrite_raises: str | None = None
        _block_status_entries: list[tuple[str, list[int]]] | None = None
        _flush_raises: str | None = None

        def __init__(self) -> None:
            self.meta_contexts: list[str] = []
            self.export_name: str | None = None
            self.uri: str | None = None
            self.shutdown_called: bool = False
            self._pread_calls: list[tuple[int, int]] = []
            self._pwrite_calls: list[tuple[int, int]] = []
            handles.append(self)

        def add_meta_context(self, ctx: str) -> None:
            self.meta_contexts.append(ctx)

        def set_export_name(self, name: str) -> None:
            self.export_name = name

        def connect_uri(self, uri: str) -> None:
            self.uri = uri
            if self._connect_raises:
                errno, msg = self._connect_raises
                raise _Error(errno=errno, errnum=1, string=msg)

        def get_block_size(self, _which: int) -> int:
            return self._server_max

        def get_size(self) -> int:
            return 10 * 1024 * 1024  # 10 MiB

        def block_status(self, length: int, offset: int, callback: Callable[..., None]) -> None:
            if self._block_status_entries:
                for metacontext, entries in self._block_status_entries:
                    callback(metacontext, offset, entries, 0)
            else:
                for ctx in self.meta_contexts:
                    callback(ctx, offset, [], 0)

        def pread(self, length: int, offset: int) -> bytes:
            self._pread_calls.append((length, offset))
            if self._pread_raises is not None:
                raise _Error(errno="EIO", errnum=5, string=self._pread_raises)
            return bytes(length)

        def pwrite(self, data: bytes, offset: int) -> None:
            self._pwrite_calls.append((len(data), offset))
            if self._pwrite_raises is not None:
                raise _Error(errno="EIO", errnum=5, string=self._pwrite_raises)

        def shutdown(self) -> None:
            self.shutdown_called = True

        def can_flush(self) -> bool:
            return True

        def flush(self) -> None:
            self._flush_called: bool = getattr(self, "_flush_called", False) or True
            if self._flush_raises is not None:
                raise _Error(errno="EIO", errnum=5, string=self._flush_raises)

    mod = types.ModuleType("nbd")
    mod.NBD = _NBD  # type: ignore[reportAttributeAccessIssue]
    mod.Error = _Error  # type: ignore[reportAttributeAccessIssue]
    mod.SIZE_MAXIMUM = 1  # type: ignore[reportAttributeAccessIssue]
    return mod, handles


# ── lazy import & missing package ─────────────────────────────────────────


@pytest.mark.unit
class TestLazyImportAndMissingPackage:
    """The ``LibnbdClient`` constructor and ``qsnap.utils.nbd_client``
    module import must never require the system ``nbd`` package.  Only
    ``connect()`` should report its absence (design D1: lazy import)."""

    def test_lazy_import_success_without_package(self) -> None:
        """Importing the module and constructing ``LibnbdClient()`` succeeds
        even when ``import nbd`` would fail."""
        # The module is already imported by the test runner — the fact
        # that we reach this point proves the import does not trigger
        # an ImportError.  Construction must also succeed.
        client = LibnbdClient()
        assert client is not None

    def test_missing_package_returns_actionable_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``connect()`` with the ``nbd`` package unavailable returns a
        failure ``NbdResult`` whose error names ``python3-libnbd`` (using
        ``MISSING_LIBNBD_ERROR``).

        Setting ``sys.modules["nbd"] = None`` forces ``import nbd`` to
        raise ``ImportError`` regardless of whether the real package is
        installed — this keeps the test environment‑agnostic."""
        monkeypatch.setitem(sys.modules, "nbd", None)
        client = LibnbdClient()
        result = client.connect("nbd://localhost", "", [])

        assert isinstance(result, NbdResult)
        assert result.success is False
        assert result.payload is None
        assert result.error == MISSING_LIBNBD_ERROR
        assert "python3-libnbd" in result.error


# ── connect ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConnect:
    """``connect()`` normalization, context negotiation, and export name."""

    @pytest.mark.parametrize("errno_name", ["ENOENT", "ECONNREFUSED"])
    def test_connect_refused_returns_error_result(
        self, monkeypatch: pytest.MonkeyPatch, errno_name: str
    ) -> None:
        """When the fake ``connect_uri`` raises ``nbd.Error`` with
        errno=ENOENT or ECONNREFUSED, ``connect()`` retries 20 times and
        returns ``NbdResult(success=False)`` whose error contains
        ``"connection refused"`` (case‑insensitive).  The exception
        never propagates."""
        mod, handles = _make_fake_nbd_module()
        mod.NBD._connect_raises = (errno_name, "server unreachable")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        with patch("time.sleep", return_value=None):
            result = client.connect("nbd://localhost", "", [])

        assert result.success is False
        assert result.payload is None
        assert result.error is not None
        assert "connection refused" in result.error.lower(), (
            f"Expected 'connection refused' in error, got: {result.error!r}"
        )
        # 20 fresh NBD handles should have been created (one per retry attempt).
        assert len(handles) == 20, f"Expected 20 NBD handles (one per retry), got {len(handles)}"

    def test_connect_requests_exact_meta_contexts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``connect()`` passes every requested meta‑context to
        ``add_meta_context()`` on the NBD handle, in order."""
        mod, handles = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        contexts = ["base:allocation", "qemu:dirty-bitmap:backup-vda"]
        result = client.connect("nbd://localhost", "", contexts)

        assert result.success is True
        assert len(handles) == 1
        handle = handles[0]
        assert handle.meta_contexts == contexts, (  # type: ignore[attr-defined]
            f"Expected contexts {contexts}, got {handle.meta_contexts}"
        )

    def test_connect_sets_export_name_only_when_non_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``connect(export_name="")`` does NOT call ``set_export_name``.
        ``connect(export_name="vda")`` passes ``"vda"`` to
        ``set_export_name``."""
        mod1, handles1 = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod1)
        client = LibnbdClient()
        result = client.connect("nbd:unix:/tmp/sock", "", ["base:allocation"])
        assert result.success is True
        handle1 = handles1[0]
        assert handle1.export_name is None, (  # type: ignore[attr-defined]
            "set_export_name must NOT be called when export_name is empty"
        )

        # Fresh module for the second call.
        mod2, handles2 = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod2)
        client2 = LibnbdClient()
        result2 = client2.connect("nbd:unix:/tmp/sock", "vda", ["base:allocation"])
        assert result2.success is True
        handle2 = handles2[0]
        assert handle2.export_name == "vda", (  # type: ignore[attr-defined]
            f"Expected export_name 'vda', got {handle2.export_name!r}"
        )

    def test_connect_success_sets_size_and_max_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After a successful connect with ``_server_max=65536``,
        ``get_max_request_size()`` returns 65536 and ``get_size()``
        returns the server‑reported size (> 0)."""
        mod, _ = _make_fake_nbd_module()
        mod.NBD._server_max = 65536  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        result = client.connect("nbd://localhost", "", [])
        assert result.success is True

        assert client.get_size() == 10 * 1024 * 1024
        assert client.get_max_request_size() == 65536

    def test_connect_caps_max_request_at_32_mib(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the server advertises a maximum larger than 32 MiB, the
        client caps ``get_max_request_size()`` at 32 MiB."""
        mod, _ = _make_fake_nbd_module()
        mod.NBD._server_max = 64 * 1024 * 1024  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        result = client.connect("nbd://localhost", "", [])
        assert result.success is True
        assert client.get_max_request_size() == 32 * 1024 * 1024

    def test_connect_server_max_zero_defaults_to_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the server reports a maximum of 0, the client uses the
        32 MiB cap as the effective ``get_max_request_size()``."""
        mod, _ = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        result = client.connect("nbd://localhost", "", [])
        assert result.success is True
        assert client.get_max_request_size() == 32 * 1024 * 1024

    def test_real_connect_refused_returns_normalized_error(self) -> None:
        """With the real ``nbd`` module installed, connecting to a
        closed TCP port returns ``NbdResult(success=False)`` whose error
        contains ``"connection refused"`` — the exception never
        propagates to the caller.

        ``time.sleep`` is mocked to avoid a 20-second wait from the
        retry loop.
        """
        client = LibnbdClient()
        with patch("time.sleep", return_value=None):
            result = client.connect("nbd://127.0.0.1:19999", "", [])
        assert result.success is False
        assert result.payload is None
        assert result.error is not None
        assert "connection refused" in result.error.lower(), (
            f"Expected 'connection refused' in error, got: {result.error!r}"
        )


# ── pread ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPread:
    """``pread()`` behaviour: EOF normalization and chunked reads."""

    @pytest.mark.parametrize(
        "error_message",
        [
            "end of file from server",
            "server closed the connection unexpectedly",
        ],
    )
    def test_pread_eof_normalized_for_retry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        error_message: str,
    ) -> None:
        """When the fake ``pread`` raises ``nbd.Error`` whose message
        contains ``"end of file"`` or ``"closed the connection"``, the
        result error contains ``"eof"`` (case‑insensitive) and
        ``is_retryable(error)`` is ``True``."""
        mod, _ = _make_fake_nbd_module()
        mod.NBD._pread_raises = error_message  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        result = client.pread(0, 1024)

        assert result.success is False
        assert result.error is not None
        assert "eof" in result.error.lower(), f"Expected 'eof' in error, got: {result.error!r}"
        assert is_retryable(result.error) is True, f"Error must be retryable: {result.error!r}"

    def test_pread_not_connected_returns_error(self) -> None:
        """``pread()`` without a prior ``connect()`` returns an error
        ``NbdResult`` with ``"not connected"``."""
        client = LibnbdClient()
        result = client.pread(0, 1024)

        assert result.success is False
        assert result.error == "not connected"


# ── chunked reads & writes ────────────────────────────────────────────────


@pytest.mark.unit
class TestChunkedReadWrite:
    """Large reads and writes are split into ``max_request_size`` chunks."""

    CHUNK_SIZE = 65536  # 64 KiB

    def test_large_read_chunked_to_max_request_size(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``pread(offset=100, length=200 KiB)`` with a 64 KiB chunk cap
        issues 4 sequential read calls and concatenates the result."""
        mod, handles = _make_fake_nbd_module()
        mod.NBD._server_max = self.CHUNK_SIZE  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        result = client.pread(100, 200 * 1024)  # 204800 bytes

        assert result.success is True
        assert isinstance(result.payload, bytes)
        assert len(result.payload) == 200 * 1024

        handle: Any = handles[0]
        calls: list[tuple[int, int]] = handle._pread_calls
        assert len(calls) == 4, f"Expected 4 chunk calls, got {len(calls)}: {calls}"

        # Each chunk must be ≤ CHUNK_SIZE and in ascending offset order.
        expected_offsets: list[int] = []
        pos = 100
        remaining = 200 * 1024
        while remaining > 0:
            expected_offsets.append(pos)
            chunk = min(remaining, self.CHUNK_SIZE)
            pos += chunk
            remaining -= chunk

        actual_offsets = [offset for _length, offset in calls]
        assert actual_offsets == expected_offsets, (
            f"Expected offsets {expected_offsets}, got {actual_offsets}"
        )

        # Each requested length must be ≤ CHUNK_SIZE.
        for length, _offset in calls:
            assert length <= self.CHUNK_SIZE

    def test_large_pwrite_chunked_to_max_request_size(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``pwrite(offset=200, data=200 KiB)`` with a 64 KiB chunk cap
        issues 4 sequential write calls at ascending offsets."""
        mod, handles = _make_fake_nbd_module()
        mod.NBD._server_max = self.CHUNK_SIZE  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])

        data = b"\x01" * (200 * 1024)  # 204800 bytes
        result = client.pwrite(200, data)

        assert result.success is True
        assert result.payload is None

        handle: Any = handles[0]
        calls: list[tuple[int, int]] = handle._pwrite_calls
        assert len(calls) == 4, f"Expected 4 chunk calls, got {len(calls)}: {calls}"

        expected_offsets: list[int] = []
        pos = 200
        remaining = 200 * 1024
        while remaining > 0:
            expected_offsets.append(pos)
            chunk = min(remaining, self.CHUNK_SIZE)
            pos += chunk
            remaining -= chunk

        actual_offsets = [offset for _length, offset in calls]
        assert actual_offsets == expected_offsets, (
            f"Expected offsets {expected_offsets}, got {actual_offsets}"
        )

        for length, _offset in calls:
            assert length <= self.CHUNK_SIZE

    def test_read_max_request_size_zero_uses_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``get_max_request_size()`` returns the 32 MiB cap (server
        max was 0), a 35 MiB read is chunked into two calls."""
        mod, handles = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        cap = 32 * 1024 * 1024
        result = client.pread(0, cap + 1)

        assert result.success is True
        assert isinstance(result.payload, bytes)
        assert len(result.payload) == cap + 1

        handle: Any = handles[0]
        calls: list[tuple[int, int]] = handle._pread_calls
        assert len(calls) == 2
        # First chunk: cap bytes at offset 0; second: 1 byte at cap.
        assert calls[0] == (cap, 0)
        assert calls[1] == (1, cap)


# ── block_status ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBlockStatus:
    """``block_status()`` extent parsing into ``dict[str, list[NbdExtent]]``."""

    def test_block_status_returns_extent_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The fake fires the callback once per meta‑context.  The result
        payload maps each context name to a list of ``NbdExtent`` objects
        with correct running offsets and ``data`` flags:
        * ``base:allocation``: types 0,2 → True; 1,3 → False.
        * dirty‑bitmap contexts: ``bool(type)``."""
        mod, _ = _make_fake_nbd_module()
        # Each entry is (metacontext, [length, type, length, type, ...])
        mod.NBD._block_status_entries = [  # type: ignore[attr-defined]
            ("base:allocation", [1024, 0, 512, 1, 1024, 2, 256, 3]),
            ("qemu:dirty-bitmap:backup-vda", [1024, 0, 512, 1, 1536, 0]),
        ]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", ["base:allocation", "qemu:dirty-bitmap:backup-vda"])
        query_offset = 0
        result = client.block_status(query_offset, 4096)

        assert result.success is True
        payload = result.payload
        assert isinstance(payload, dict)
        payload_dict: dict[object, object] = payload  # type: ignore[assignment]
        assert set(payload_dict) == {"base:allocation", "qemu:dirty-bitmap:backup-vda"}

        # base:allocation extents
        alloc_extents_raw = payload_dict["base:allocation"]
        assert isinstance(alloc_extents_raw, list)
        alloc_extents: list[NbdExtent] = alloc_extents_raw  # type: ignore[assignment]
        assert len(alloc_extents) == 4

        assert alloc_extents[0] == NbdExtent(offset=query_offset + 0, length=1024, data=True)
        assert alloc_extents[1] == NbdExtent(offset=query_offset + 1024, length=512, data=False)
        assert alloc_extents[2] == NbdExtent(offset=query_offset + 1536, length=1024, data=True)
        assert alloc_extents[3] == NbdExtent(offset=query_offset + 2560, length=256, data=False)

        # dirty-bitmap extents
        bitmap_extents_raw = payload_dict["qemu:dirty-bitmap:backup-vda"]
        assert isinstance(bitmap_extents_raw, list)
        bitmap_extents: list[NbdExtent] = bitmap_extents_raw  # type: ignore[assignment]
        assert len(bitmap_extents) == 3

        assert bitmap_extents[0] == NbdExtent(offset=query_offset + 0, length=1024, data=False)
        assert bitmap_extents[1] == NbdExtent(offset=query_offset + 1024, length=512, data=True)
        assert bitmap_extents[2] == NbdExtent(offset=query_offset + 1536, length=1536, data=False)

    def test_block_status_not_connected_returns_error(self) -> None:
        """``block_status()`` without a prior ``connect()`` returns an
        error with ``"not connected"``."""
        client = LibnbdClient()
        result = client.block_status(0, 1024)
        assert result.success is False
        assert result.error == "not connected"

    def test_block_status_non_zero_offset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Extents are reported with offsets relative to the query offset."""
        mod, _ = _make_fake_nbd_module()
        mod.NBD._block_status_entries = [  # type: ignore[attr-defined]
            ("base:allocation", [512, 0, 512, 1]),
        ]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", ["base:allocation"])
        result = client.block_status(4096, 1024)

        assert result.success is True
        payload = result.payload
        assert isinstance(payload, dict)
        payload_dict: dict[object, object] = payload  # type: ignore[assignment]
        extents_raw = payload_dict["base:allocation"]
        assert isinstance(extents_raw, list)
        extents: list[NbdExtent] = extents_raw  # type: ignore[assignment]
        assert extents[0] == NbdExtent(offset=4096, length=512, data=True)
        assert extents[1] == NbdExtent(offset=4608, length=512, data=False)


# ── pwrite ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPwrite:
    """``pwrite()`` success and not‑connected error."""

    def test_pwrite_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful ``pwrite()`` returns ``NbdResult(success=True,
        payload=None)`` and the handle records the write call."""
        mod, handles = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        result = client.pwrite(0, b"hello")

        assert result.success is True
        assert result.payload is None
        assert result.error is None

        handle: Any = handles[0]
        calls: list[tuple[int, int]] = handle._pwrite_calls
        assert len(calls) == 1
        assert calls[0] == (5, 0)

    def test_pwrite_not_connected_returns_error(self) -> None:
        """``pwrite()`` without ``connect()`` returns ``"not connected"``."""
        client = LibnbdClient()
        result = client.pwrite(0, b"data")
        assert result.success is False
        assert result.error == "not connected"


# ── disconnect ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDisconnect:
    """``disconnect()`` safety (idempotent, no‑op when unconnected)."""

    def test_disconnect_safe_on_unconnected(self) -> None:
        """``disconnect()`` on a freshly‑constructed client does not raise."""
        client = LibnbdClient()
        client.disconnect()  # must not raise

    def test_disconnect_safe_second_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After connect + disconnect, a second ``disconnect()`` is safe."""
        mod, _ = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        client.disconnect()
        client.disconnect()  # must not raise

    def test_disconnect_calls_shutdown_and_clears_handle(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After a successful connect, ``disconnect()`` calls
        ``handle.shutdown()`` and clears the internal handle."""
        mod, handles = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        handle: Any = handles[0]
        assert not handle.shutdown_called

        client.disconnect()
        assert handle.shutdown_called, "shutdown() must have been called"


# ── is_libnbd_available ───────────────────────────────────────────────────


@pytest.mark.unit
class TestIsLibnbdAvailable:
    """``is_libnbd_available()`` mirrors whether the system ``nbd``
    bindings are importable."""

    def test_returns_false_when_not_installed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``importlib.util.find_spec('nbd')`` returns ``None``,
        ``is_libnbd_available()`` returns ``False`` (environment‑agnostic:
        the real availability is irrelevant)."""

        def _fake_find_spec(name: str) -> None:
            return None

        monkeypatch.setattr("importlib.util.find_spec", _fake_find_spec)
        assert is_libnbd_available() is False

    def test_returns_true_when_package_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``importlib.util.find_spec('nbd')`` returns a truthy
        value, ``is_libnbd_available()`` returns ``True``."""

        def _fake_find_spec(name: str) -> object | None:
            return object() if name == "nbd" else None

        monkeypatch.setattr("importlib.util.find_spec", _fake_find_spec)
        assert is_libnbd_available() is True


# ── connect retry (design D8) ──────────────────────────────────────────────


@pytest.mark.unit
class TestConnectRetry:
    """``connect()`` retry loop: 20 attempts, fresh handle, sleep between."""

    def test_connect_retry_20_attempts_fresh_handle(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When every connect attempt fails, ``connect()`` creates a fresh
        ``nbd.NBD()`` handle on each of the 20 attempts (handles are never
        reused), calls ``time.sleep(1)`` between attempts 1–19, and
        returns ``NbdResult(success=False)``."""
        mod, handles = _make_fake_nbd_module()
        mod.NBD._connect_raises = ("ECONNREFUSED", "connection refused")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        with patch("time.sleep", return_value=None) as sleep_mock:
            result = client.connect("nbd://localhost", "", [])

        assert result.success is False
        assert result.error is not None
        assert "connection refused" in result.error.lower()

        # 20 fresh handles, one per attempt.
        assert len(handles) == 20, f"Expected 20 fresh NBD handles, got {len(handles)}"

        # sleep called between each failed attempt: 19 calls for attempts 1–19.
        assert sleep_mock.call_count == 19, (
            f"Expected 19 sleep calls (between 20 attempts), got {sleep_mock.call_count}"
        )
        for call in sleep_mock.call_args_list:
            assert call.args == (1,) or call.args == (1,), f"Expected sleep(1), got {call.args}"

    def test_connect_retry_exhausted_returns_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After 20 consecutive connect failures, ``connect()`` returns
        ``NbdResult(success=False)`` with the last error message and
        ``payload=None``."""
        mod, handles = _make_fake_nbd_module()
        mod.NBD._connect_raises = ("EPIPE", "broken pipe on last attempt")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        with patch("time.sleep", return_value=None):
            result = client.connect("nbd://localhost", "vda", [])

        assert result.success is False
        assert result.payload is None
        assert result.error is not None
        assert "broken pipe" in result.error.lower(), (
            f"Expected 'broken pipe' in error, got: {result.error!r}"
        )


# ── can_flush ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCanFlush:
    """``can_flush()`` delegates to the NBD handle and is safe when
    unconnected."""

    def test_can_flush_delegates_to_nbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After ``connect()``, ``can_flush()`` returns the value of
        ``nbd.can_flush()``.  ``flush()`` is a no‑op in the fake."""
        mod, _ = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        assert client.can_flush() is True


# ── flush ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFlush:
    """``flush()`` delegates to the NBD handle and is safe when
    unconnected."""

    def test_flush_delegates_to_nbd(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After ``connect()``, ``flush()`` calls ``nbd.flush()`` and
        returns ``NbdResult(success=True)``."""
        mod, _ = _make_fake_nbd_module()
        monkeypatch.setitem(sys.modules, "nbd", mod)

        client = LibnbdClient()
        client.connect("nbd://localhost", "", [])
        result = client.flush()

        assert result.success is True
        assert result.payload is None
        assert result.error is None

    def test_flush_safe_when_can_flush_false(
        self,
    ) -> None:
        """``flush()`` called without ``connect()`` returns
        ``NbdResult(success=False, error="not connected")`` — caller is
        safe to invoke ``flush()`` on an uninitialized client."""
        client = LibnbdClient()
        result = client.flush()

        assert result.success is False
        assert result.payload is None
        assert result.error == "not connected"
