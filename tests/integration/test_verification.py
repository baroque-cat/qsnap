"""Integration tests for ``verify_backup()`` with real qcow2 files.

All tests in this module are marked ``@pytest.mark.integration``.
They require ``qemu-img`` but do NOT require a running libvirt daemon
— they create real qcow2 files in temporary directories and run
``verify_backup()`` with real shell commands.

Run only when explicitly requested::

    poetry run pytest tests/integration/ -m integration
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.hash import file_sha256
from qsnap.utils.verification import verify_backup

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _create_qcow2_disk(path: Path, size: str = "10M") -> bytes:
    """Create a tiny qcow2 disk at *path* with given *size*.

    Uses ``qemu-io`` to write a payload into the qcow2 virtual disk so
    the file has distinguishable content without corrupting the qcow2
    header.  Returns the payload bytes written.
    """
    shell = SubprocessShell()

    # Create the disk.
    create_result = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(path), size],
        timeout=30,
    )
    if not create_result.success:
        pytest.skip(f"qemu-img create failed (qemu-img not available?): {create_result.error}")

    # Write a small payload via qemu-io inside the qcow2 virtual disk.
    # This preserves the qcow2 header and format.
    payload = b"\x42" * 4096
    io_result = shell.run(
        [
            "qemu-io",
            "-c",
            "write -P 0x42 0 4096",
            str(path),
        ],
        timeout=30,
    )
    if not io_result.success:
        # qemu-io may not be available; skip with a message.
        pytest.skip(f"qemu-io write failed (qemu-io not available?): {io_result.error}")

    return payload


def _copy_qcow2(source: Path, dest: Path) -> None:
    """Copy *source* to *dest* via ``cp`` (byte-level copy)."""
    shell = SubprocessShell()
    cp_result = shell.run(["cp", str(source), str(dest)], timeout=30)
    if not cp_result.success:
        pytest.skip(f"cp failed: {cp_result.error}")


# ──────────────────────────────────────────────────────────────────────
# Test 1: Metadata verification of a real qcow2 file
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_metadata_verification_real_qcow2():
    """Verify ``verify_backup()`` with ``verify_mode="metadata"`` passes
    for two identical qcow2 files."""
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-verify-"))

    try:
        source = tmpdir / "source.qcow2"
        target = tmpdir / "target.qcow2"
        _create_qcow2_disk(source)
        _copy_qcow2(source, target)

        result = verify_backup(
            shell,
            str(source),
            str(target),
            verify_mode="metadata",
        )

        assert result is None, (
            f"Metadata verification should pass for identical qcow2 files, got: {result}"
        )
    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# Test 2: Hash verification of a real qcow2 file
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_hash_verification_real_qcow2():
    """Verify ``verify_backup()`` with ``verify_mode="hash"`` passes
    when the expected hash matches the target file."""
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-verify-"))

    try:
        source = tmpdir / "source.qcow2"
        target = tmpdir / "target.qcow2"
        _create_qcow2_disk(source)
        _copy_qcow2(source, target)

        # Compute SHA-256 of the target (which is identical to source).
        expected_hash = file_sha256(target)

        result = verify_backup(
            shell,
            str(source),
            str(target),
            verify_mode="hash",
            expected_hash=expected_hash,
        )

        assert result is None, f"Hash verification should pass when hash matches, got: {result}"
    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# Test 3: Full verification of two identical qcow2 files
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_full_verification_real_compare():
    """Verify ``verify_backup()`` with ``verify_mode="full"`` passes
    when two qcow2 files have identical virtual-disk content."""
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-verify-"))

    try:
        source = tmpdir / "source.qcow2"
        target = tmpdir / "target.qcow2"
        _create_qcow2_disk(source)
        _copy_qcow2(source, target)

        result = verify_backup(
            shell,
            str(source),
            str(target),
            verify_mode="full",
        )

        assert result is None, f"Full verification should pass for identical content, got: {result}"
    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# Test 4: Full verification detects corruption (simulated race condition)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_full_verify_detects_race_condition_corruption():
    """Verify ``verify_backup()`` with ``verify_mode="full"`` detects
    corruption when the target file is modified after copy (simulating
    a race condition)."""
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-verify-"))

    try:
        source = tmpdir / "source.qcow2"
        target = tmpdir / "target.qcow2"
        _create_qcow2_disk(source)
        _copy_qcow2(source, target)

        # Modify target: use qemu-io to write different data into the
        # qcow2 virtual disk, which qemu-img compare will detect.
        shell.run(
            [
                "qemu-io",
                "-c",
                "write -P 0x00 0 512",
                str(target),
            ],
            timeout=30,
        )

        result = verify_backup(
            shell,
            str(source),
            str(target),
            verify_mode="full",
        )

        # The result should be non-None indicating a mismatch or corruption.
        assert result is not None, (
            "Full verification should detect corruption when target differs from source"
        )
        result_lower = result.lower()
        assert "mismatch" in result_lower or "content" in result_lower, (
            f"Verification failure should mention 'mismatch' or 'content', got: {result!r}"
        )
    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# Test 5: Failed file is deleted after verify failure
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_int_failed_file_deleted_after_verify_failure():
    """Simulate a verify failure and assert the target file is deleted
    by the caller (rm -f pattern used by backup providers)."""
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-int-verify-"))

    try:
        source = tmpdir / "source.qcow2"
        target = tmpdir / "target.qcow2"
        _create_qcow2_disk(source)
        _copy_qcow2(source, target)

        # Corrupt the target: write different data into the qcow2
        # virtual disk using qemu-io (preserves qcow2 header).
        shell.run(
            [
                "qemu-io",
                "-c",
                "write -P 0x00 0 512",
                str(target),
            ],
            timeout=30,
        )

        result = verify_backup(
            shell,
            str(source),
            str(target),
            verify_mode="full",
        )

        # Verification must have failed.
        assert result is not None, "Verification should have failed"

        # Simulate the caller's cleanup: rm -f the target file.
        rm_result = shell.run(["rm", "-f", str(target)], timeout=10)
        assert rm_result.success, f"rm -f should succeed: {rm_result.error}"

        # Verify the file is actually gone.
        assert not target.exists(), (
            f"Target file {target} should have been deleted after verification failure"
        )
    finally:
        import shutil

        shutil.rmtree(str(tmpdir), ignore_errors=True)
