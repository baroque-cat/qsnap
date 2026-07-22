"""Integration tests for ``verify_bitmap_incremental()``.

Creates real qcow2 chains via ``qemu-img`` and passes them through
``verify_bitmap_incremental()`` to exercise metadata, hash, and full
tiers.  Uses ``SubprocessShell`` for real command execution.

Guarded by ``pytest.mark.skipif(shutil.which("qemu-img") is None,
reason="qemu-img not available")`` — no libvirt or libnbd required.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from qsnap.models.results import ShellResult
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.verification import verify_bitmap_incremental


def _run_qemu_img(
    shell: SubprocessShell,
    *args: str,
    check: bool = True,
) -> ShellResult:
    """Run a qemu-img command through SubprocessShell."""
    cmd = ["qemu-img", *args]
    result = shell.run(cmd, timeout=60, check=check)
    return result


def _run_qemu_io(
    shell: SubprocessShell,
    image: Path,
    *args: str,
) -> ShellResult:
    """Run a qemu-io command through SubprocessShell."""
    cmd = ["qemu-io", "-f", "qcow2", str(image), "-c", *args]
    return shell.run(cmd, timeout=60)


def _get_info(shell: SubprocessShell, path: Path) -> dict[str, object]:
    """Return parsed JSON qemu-img info for *path*."""
    result = _run_qemu_img(shell, "info", "--output=json", str(path))
    assert result.success, f"qemu-img info failed: {result.error}"
    return json.loads(result.stdout)  # type: ignore[no-any-return]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not available")
def test_metadata_tier_passes_for_valid_delta() -> None:
    """Metadata-tier verify passes for a well-formed delta with correct
    backing and small actual-size."""
    shell = SubprocessShell()

    with tempfile.TemporaryDirectory(prefix="qsnap-int-verify-bitmap-") as td:
        tmp = Path(td)

        # 1. Create a FULL qcow2 (64 MiB virtual).
        full = tmp / "full.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(full), "64M")
        assert r.success, f"qemu-img create full failed: {r.error}"

        # 2. Create a delta chained to the full.
        delta = tmp / "delta.qcow2"
        r = _run_qemu_img(
            shell,
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full),
            "-F",
            "qcow2",
            str(delta),
        )
        assert r.success, f"qemu-img create delta failed: {r.error}"

        # 3. Write 2 MiB into the delta via qemu-io.
        r = _run_qemu_io(shell, delta, "write -P 0xAB 0 2M")
        assert r.success, f"qemu-io write failed: {r.error}"

        # 4. Verify delta info.
        delta_info = _get_info(shell, delta)
        assert delta_info.get("format") == "qcow2"
        assert delta_info.get("backing-filename") is not None

        # 5. run verify_bitmap_incremental (metadata tier).
        result = verify_bitmap_incremental(
            shell=shell,
            source_path=str(full),
            delta_path=str(delta),
            expected_backing=str(full),
            dirty_bytes=2 * 1024 * 1024,
            verify_mode="metadata",
        )
        assert result is None, f"metadata verify should pass, got: {result}"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not available")
def test_wrong_backing_fails_verify() -> None:
    """When expected_backing does not match the delta's actual backing,
    verification fails."""
    shell = SubprocessShell()

    with tempfile.TemporaryDirectory(prefix="qsnap-int-verify-bitmap-") as td:
        tmp = Path(td)

        full = tmp / "full.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(full), "64M")
        assert r.success

        delta = tmp / "delta.qcow2"
        r = _run_qemu_img(
            shell,
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full),
            "-F",
            "qcow2",
            str(delta),
        )
        assert r.success

        # Wrong backing path.
        wrong_backing = str(tmp / "nonexistent.qcow2")
        result = verify_bitmap_incremental(
            shell=shell,
            source_path=str(full),
            delta_path=str(delta),
            expected_backing=wrong_backing,
            dirty_bytes=0,
            verify_mode="metadata",
        )
        assert result is not None
        assert "backing-filename mismatch" in result
        assert wrong_backing in result


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not available")
def test_bloated_delta_fails_barrier() -> None:
    """A delta whose actual-size exceeds the barrier fails.

    With dirty_bytes=0 the barrier is exactly 64 MiB (the slack value).
    Writing the full 64 MiB virtual size produces ~64.3 MiB actual-size
    due to qcow2 metadata overhead, which trips the barrier.
    """
    shell = SubprocessShell()

    with tempfile.TemporaryDirectory(prefix="qsnap-int-verify-bitmap-") as td:
        tmp = Path(td)

        full = tmp / "full.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(full), "64M")
        assert r.success

        delta = tmp / "delta.qcow2"
        r = _run_qemu_img(
            shell,
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full),
            "-F",
            "qcow2",
            str(delta),
        )
        assert r.success

        # Write the entire 64 MiB to push actual-size over the 64 MiB
        # slack barrier (qcow2 metadata adds overhead on top).
        _run_qemu_io(shell, delta, "write -P 0xCD 0 67108864")

        result = verify_bitmap_incremental(
            shell=shell,
            source_path=str(full),
            delta_path=str(delta),
            expected_backing=str(full),
            dirty_bytes=0,
            verify_mode="metadata",
        )
        assert result is not None, "barrier should fail for bloated delta"
        assert "exceeds dirty-data barrier" in result
        assert "engine regressed to full copy" in result


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not available")
def test_hash_tier_compare_passes_for_consistent_chains() -> None:
    """Hash tier passes when a standalone source has the same virtual
    content as the full+delta chain."""
    shell = SubprocessShell()

    with tempfile.TemporaryDirectory(prefix="qsnap-int-verify-bitmap-") as td:
        tmp = Path(td)

        # 1. Full base qcow2 (64 MiB).
        full = tmp / "full.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(full), "64M")
        assert r.success

        # 2. Write a known pattern to the full.
        _run_qemu_io(shell, full, "write -P 0x55 0 4M")
        _run_qemu_io(shell, full, "write -P 0xAA 8M 4M")

        # 3. Create delta chained to full.
        delta = tmp / "delta.qcow2"
        r = _run_qemu_img(
            shell,
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full),
            "-F",
            "qcow2",
            str(delta),
        )
        assert r.success

        # 4. Write 2 MiB into the delta (overwrite first 2 MiB).
        _run_qemu_io(shell, delta, "write -P 0xFF 0 2M")
        _run_qemu_io(shell, delta, "write -P 0xEE 6M 2M")

        # 5. Create a standalone source qcow2 with the SAME resolved
        #    content as full+delta.  We do this by "committing" delta
        #    into a new standalone: qemu-img convert delta standalone.
        source = tmp / "source.qcow2"
        r = _run_qemu_img(
            shell,
            "convert",
            "-f",
            "qcow2",
            "-O",
            "qcow2",
            str(delta),
            str(source),
        )
        assert r.success, f"qemu-img convert for source failed: {r.error}"

        # 6. Run hash-tier verify: source is standalone, delta chains to full.
        #    Their virtual content should match because source is delta's
        #    resolved content flattened.
        result = verify_bitmap_incremental(
            shell=shell,
            source_path=str(source),
            delta_path=str(delta),
            expected_backing=str(full),
            dirty_bytes=4 * 1024 * 1024,
            verify_mode="hash",
        )
        assert result is None, f"hash verify should pass, got: {result}"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not available")
def test_hash_tier_compare_fails_for_divergent_chains() -> None:
    """Hash tier fails when standalone source content differs from
    full+delta resolved content."""
    shell = SubprocessShell()

    with tempfile.TemporaryDirectory(prefix="qsnap-int-verify-bitmap-") as td:
        tmp = Path(td)

        # 1. Full base qcow2 (64 MiB).
        full = tmp / "full.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(full), "64M")
        assert r.success

        # 2. Write known pattern.
        _run_qemu_io(shell, full, "write -P 0x55 0 4M")

        # 3. Delta chained to full with different content.
        delta = tmp / "delta.qcow2"
        r = _run_qemu_img(
            shell,
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full),
            "-F",
            "qcow2",
            str(delta),
        )
        assert r.success
        _run_qemu_io(shell, delta, "write -P 0xFF 1M 1M")

        # 4. Source with DIFFERENT content — write different bytes.
        source = tmp / "source.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(source), "64M")
        assert r.success
        # Write a pattern that does NOT match full+delta resolved content.
        _run_qemu_io(shell, source, "write -P 0x55 0 4M")
        _run_qemu_io(shell, source, "write -P 0x00 1M 1M")  # different from delta's 0xFF

        # 5. Hash-tier compare should fail.
        result = verify_bitmap_incremental(
            shell=shell,
            source_path=str(source),
            delta_path=str(delta),
            expected_backing=str(full),
            dirty_bytes=4 * 1024 * 1024,
            verify_mode="hash",
        )
        assert result is not None
        assert "content comparison mismatch" in result


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not available")
def test_full_tier_compare_passes_for_consistent_chains() -> None:
    """Full tier passes when standalone source matches delta chain content."""
    shell = SubprocessShell()

    with tempfile.TemporaryDirectory(prefix="qsnap-int-verify-bitmap-") as td:
        tmp = Path(td)

        full = tmp / "full.qcow2"
        r = _run_qemu_img(shell, "create", "-f", "qcow2", str(full), "64M")
        assert r.success
        _run_qemu_io(shell, full, "write -P 0x11 0 2M")
        _run_qemu_io(shell, full, "write -P 0x22 10M 2M")

        delta = tmp / "delta.qcow2"
        r = _run_qemu_img(
            shell,
            "create",
            "-f",
            "qcow2",
            "-b",
            str(full),
            "-F",
            "qcow2",
            str(delta),
        )
        assert r.success
        _run_qemu_io(shell, delta, "write -P 0x33 4M 2M")

        # Convert delta to standalone source for consistent content.
        source = tmp / "source.qcow2"
        r = _run_qemu_img(
            shell,
            "convert",
            "-f",
            "qcow2",
            "-O",
            "qcow2",
            str(delta),
            str(source),
        )
        assert r.success

        result = verify_bitmap_incremental(
            shell=shell,
            source_path=str(source),
            delta_path=str(delta),
            expected_backing=str(full),
            dirty_bytes=4 * 1024 * 1024,
            verify_mode="full",
        )
        assert result is None, f"full verify should pass, got: {result}"
