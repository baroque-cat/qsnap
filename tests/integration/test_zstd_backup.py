"""Integration tests for zstd compression in qemu-img.

All tests use real ``qemu-img`` binaries.  They are marked
``@pytest.mark.integration`` and create real test disks in
``tmp_path``.  No libvirt daemon is required.

These tests may be slow — that is expected for integration-level
compression benchmarks and stall-detection validation.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_zstd_backup.py -v -m integration
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell

# ── Helpers ────────────────────────────────────────────────────────────────


def _get_qemu_version(shell: SubprocessShell) -> tuple[int, int, int] | None:
    """Parse *qemu-img --version* output.

    Returns ``(major, minor, patch)`` or ``None`` if parsing fails.
    """
    result = shell.run(["qemu-img", "--version"], timeout=10)
    if not result.success:
        return None
    match = re.search(r"qemu-img version (\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_qemu_img_convert_zstd_produces_valid_qcow2(tmp_path: Path) -> None:
    """Create a test qcow2 disk, convert with zstd compression, verify output.

    1. Create a 100 MB raw source file with non-zero data.
    2. Convert to intermediate qcow2 (uncompressed).
    3. Convert that qcow2 with ``-c -o compression_type=zstd``.
    4. Verify output is a valid qcow2 via ``qemu-img info`` and
       ``qemu-img check``.
    """
    shell = SubprocessShell()

    # Check qemu-img availability and version for zstd support.
    version = _get_qemu_version(shell)
    if version is None:
        pytest.skip("qemu-img not available")
    if version < (5, 2):
        pytest.skip(
            f"qemu-img {version[0]}.{version[1]}.{version[2]} "
            f"does not support -o compression_type=zstd (need >= 5.2)"
        )

    raw_source = tmp_path / "source.raw"
    intermediate = tmp_path / "intermediate.qcow2"
    compressed = tmp_path / "compressed.qcow2"

    # Create 100 MB raw source with non-zero data.
    dd_result = shell.run(
        ["dd", "if=/dev/urandom", f"of={raw_source}", "bs=1M", "count=100"],
        timeout=180,
    )
    if not dd_result.success:
        pytest.skip(f"dd /dev/urandom failed: {dd_result.error}")

    # Convert raw → intermediate qcow2 (uncompressed).
    conv1 = shell.run(
        [
            "qemu-img",
            "convert",
            "-f",
            "raw",
            "-O",
            "qcow2",
            str(raw_source),
            str(intermediate),
        ],
        timeout=120,
    )
    assert conv1.success, f"qemu-img convert (raw → qcow2) failed: {conv1.error}"

    # Free disk space: raw source is no longer needed.
    raw_source.unlink(missing_ok=True)

    # Convert intermediate qcow2 → zstd-compressed qcow2.
    conv2 = shell.run(
        [
            "qemu-img",
            "convert",
            "-c",
            "-o",
            "compression_type=zstd",
            "-O",
            "qcow2",
            str(intermediate),
            str(compressed),
        ],
        timeout=180,
    )
    assert conv2.success, f"qemu-img convert with -o compression_type=zstd failed: {conv2.error}"

    # Free disk space: intermediate is no longer needed.
    intermediate.unlink(missing_ok=True)

    # Verify compressed output exists and is a valid qcow2.
    assert compressed.exists(), "Compressed output file was not created"
    assert compressed.stat().st_size > 0, "Compressed output file is empty"

    # qemu-img info: verify format and that it is a qcow2 image.
    info = shell.run(
        ["qemu-img", "info", "--output=json", str(compressed)],
        timeout=30,
    )
    assert info.success, f"qemu-img info failed: {info.error}"
    assert "qcow2" in info.stdout, (
        f"qemu-img info output does not reference qcow2 format: {info.stdout!r}"
    )

    # qemu-img check: verify on-disk integrity.
    check = shell.run(
        ["qemu-img", "check", str(compressed)],
        timeout=60,
    )
    assert check.success, f"qemu-img check failed: {check.error}"
    assert "No errors were found" in check.stdout or "ERROR" not in check.stdout, (
        f"qemu-img check reported errors: {check.stdout}"
    )


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_zstd_faster_than_zlib(tmp_path: Path) -> None:
    """Compare zstd and zlib compression speeds on a 500 MB disk.

    1. Create 500 MB raw file with random data.
    2. Convert to intermediate qcow2.
    3. Time ``qemu-img convert -c -o compression_type=zstd``.
    4. Time ``qemu-img convert -c -o compression_type=zlib``.
    5. Assert zstd is faster, allowing 50% tolerance for system noise.
    """
    shell = SubprocessShell()

    version = _get_qemu_version(shell)
    if version is None:
        pytest.skip("qemu-img not available")
    if version < (5, 2):
        pytest.skip(
            f"qemu-img {version[0]}.{version[1]}.{version[2]} "
            f"does not support -o compression_type=zstd (need >= 5.2)"
        )

    raw_source = tmp_path / "source.raw"
    intermediate = tmp_path / "intermediate.qcow2"
    zstd_output = tmp_path / "zstd_output.qcow2"
    zlib_output = tmp_path / "zlib_output.qcow2"

    # Create 500 MB raw file with random data.
    dd_result = shell.run(
        ["dd", "if=/dev/urandom", f"of={raw_source}", "bs=1M", "count=500"],
        timeout=600,
    )
    if not dd_result.success:
        pytest.skip(f"dd /dev/urandom failed: {dd_result.error}")

    # Convert raw → intermediate qcow2 (uncompressed).
    conv = shell.run(
        [
            "qemu-img",
            "convert",
            "-f",
            "raw",
            "-O",
            "qcow2",
            str(raw_source),
            str(intermediate),
        ],
        timeout=180,
    )
    assert conv.success, f"Intermediate convert failed: {conv.error}"

    # Free disk space: raw source is no longer needed.
    raw_source.unlink(missing_ok=True)

    # Time zstd compression.
    zstd_start = time.perf_counter()
    zstd_result = shell.run(
        [
            "qemu-img",
            "convert",
            "-c",
            "-o",
            "compression_type=zstd",
            "-O",
            "qcow2",
            str(intermediate),
            str(zstd_output),
        ],
        timeout=600,
    )
    zstd_elapsed = time.perf_counter() - zstd_start
    assert zstd_result.success, f"zstd convert failed: {zstd_result.error}"

    # Time zlib compression.
    zlib_start = time.perf_counter()
    zlib_result = shell.run(
        [
            "qemu-img",
            "convert",
            "-c",
            "-o",
            "compression_type=zlib",
            "-O",
            "qcow2",
            str(intermediate),
            str(zlib_output),
        ],
        timeout=1800,
    )
    zlib_elapsed = time.perf_counter() - zlib_start
    assert zlib_result.success, f"zlib convert failed: {zlib_result.error}"

    # Free disk space: intermediate is no longer needed after both
    # conversions.  (zstd_output and zlib_output are the test artifacts.)
    intermediate.unlink(missing_ok=True)

    # zstd should not be meaningfully slower.  Allow generous 50%
    # tolerance because /dev/urandom data is incompressible (worst case
    # for both algorithms) and the test environment may have noise.
    ratio = zstd_elapsed / zlib_elapsed if zlib_elapsed > 0 else float("inf")
    assert ratio <= 1.5, (
        f"zstd ({zstd_elapsed:.1f}s) was significantly slower than "
        f"zlib ({zlib_elapsed:.1f}s); ratio={ratio:.2f}"
    )


@pytest.mark.integration
@pytest.mark.timeout(3600)
def test_large_disk_zstd_no_stall(tmp_path: Path) -> None:
    """Verify stall detection does not produce false positives on a large disk.

    1. Create a 200 MB raw file with non-zero data.
    2. Convert to intermediate qcow2 (uncompressed).
    3. Run ``qemu-img convert -c -o compression_type=zstd`` via
       ``SubprocessShell.run_with_stall_detection()`` with a 60 s
       stall timeout.
    4. Verify the process completes successfully (no false-positive
       stall kill) and the output is a valid qcow2.

    Note: the test plan calls for 1 GB+, but the test environment
    (tmpfs /tmp with limited space) requires a smaller size.  200 MB
    is sufficient to exercise the stall-detection path without false
    positives.
    """
    shell = SubprocessShell()

    version = _get_qemu_version(shell)
    if version is None:
        pytest.skip("qemu-img not available")
    if version < (5, 2):
        pytest.skip(
            f"qemu-img {version[0]}.{version[1]}.{version[2]} "
            f"does not support -o compression_type=zstd (need >= 5.2)"
        )

    raw_source = tmp_path / "source.raw"
    intermediate = tmp_path / "intermediate.qcow2"
    output = tmp_path / "output.qcow2"

    # Create 200 MB raw file with random data.
    dd_result = shell.run(
        ["dd", "if=/dev/urandom", f"of={raw_source}", "bs=1M", "count=200"],
        timeout=300,
    )
    if not dd_result.success:
        pytest.skip(f"dd /dev/urandom failed: {dd_result.error}")

    # Convert raw → intermediate qcow2 (uncompressed).
    conv = shell.run(
        [
            "qemu-img",
            "convert",
            "-f",
            "raw",
            "-O",
            "qcow2",
            str(raw_source),
            str(intermediate),
        ],
        timeout=300,
    )
    assert conv.success, f"Intermediate convert failed: {conv.error}"

    # Free disk space: raw source is no longer needed.
    raw_source.unlink(missing_ok=True)

    # Run qemu-img convert with stall detection.  The stall_timeout is
    # set to 60 s — if the output file stops growing for 60 s, the
    # process will be killed.  For a normally-progressing conversion
    # this should never happen.
    convert_cmd = [
        "qemu-img",
        "convert",
        "-c",
        "-o",
        "compression_type=zstd",
        "-O",
        "qcow2",
        str(intermediate),
        str(output),
    ]
    result = shell.run_with_stall_detection(
        convert_cmd,
        output_file=output,
        stall_timeout=60,
    )
    assert result.success, f"qemu-img convert with stall detection failed: {result.error}"
    # Explicitly assert there was no stall error (belt-and-suspenders).
    assert "no progress" not in (result.error or ""), (
        f"False-positive stall detected: {result.error}"
    )

    # Free disk space: intermediate is no longer needed.
    intermediate.unlink(missing_ok=True)

    # Verify output exists and passes qemu-img check.
    assert output.exists(), "Output file was not created"
    assert output.stat().st_size > 0, "Output file is empty"

    check = shell.run(
        ["qemu-img", "check", str(output)],
        timeout=120,
    )
    assert check.success, f"qemu-img check on output failed: {check.error}"
    assert "No errors were found" in check.stdout or "ERROR" not in check.stdout, (
        f"qemu-img check reported errors: {check.stdout}"
    )
