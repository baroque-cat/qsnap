"""Integration tests for ``scan_backing_chain()`` with real qcow2 backing chains.

All tests in this module require ``qemu-img`` to be available (no
libvirt daemon needed).  They are marked ``@pytest.mark.integration``
and use the ``test_vm`` fixture from ``conftest.py`` for the temp
directory infrastructure.

The ``scan_backing_chain()`` function (from ``qsnap.utils.verification``)
parses ``qemu-img info --force-share --backing-chain --output=json``
and verifies:

(a) Every file exists on the filesystem.
(b) Every file has format ``"qcow2"``.
(c) Backing-filename references are consistent.
(d) No cycles.

These tests create real qcow2 chains via ``qemu-img create -b`` and
verify the function's behavior on intact, broken, and non-qcow2 chains.

Run only when explicitly requested::

    poetry run pytest tests/integration/test_scan_backing_chain_real_chain.py -v -m integration
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.verification import scan_backing_chain

pytestmark = pytest.mark.integration


# ── helpers ──────────────────────────────────────────────────────────


def _create_qcow2_chain(
    shell: SubprocessShell,
    work_dir: Path,
    chain_names: list[str],
) -> list[Path]:
    """Create a qcow2 backing chain: ``chain_names[0]`` ← ... ← ``chain_names[-1]``.

    The first entry is the base (no backing file).  Each subsequent entry
    is created with ``-b`` pointing to the previous entry.  The last entry
    is the active/top layer.

    Returns a list of ``Path`` objects in creation order (base → top).
    """
    paths: list[Path] = []
    for i, name in enumerate(chain_names):
        file_path = work_dir / name
        if i == 0:
            # Base — no backing file.
            result = shell.run(
                ["qemu-img", "create", "-f", "qcow2", str(file_path), "64K"],
                timeout=30,
            )
            assert result.success, f"Failed to create base {name}: {result.error}"
        else:
            backing = paths[i - 1]
            result = shell.run(
                [
                    "qemu-img",
                    "create",
                    "-f",
                    "qcow2",
                    "-b",
                    str(backing),
                    "-F",
                    "qcow2",
                    str(file_path),
                    "64K",
                ],
                timeout=30,
            )
            assert result.success, f"Failed to create {name}: {result.error}"
        paths.append(file_path)
    return paths


def _create_raw_file(shell: SubprocessShell, work_dir: Path, name: str) -> Path:
    """Create a raw-format file (not qcow2) at *work_dir* / *name*."""
    file_path = work_dir / name
    result = shell.run(
        ["qemu-img", "create", "-f", "raw", str(file_path), "64K"],
        timeout=30,
    )
    assert result.success, f"Failed to create raw file {name}: {result.error}"
    return file_path


# ──────────────────────────────────────────────────────────────────────
# Test 1: Intact chain — scan_backing_chain returns success, no broken files
# ──────────────────────────────────────────────────────────────────────


def test_scan_backing_chain_intact(test_vm):
    """Scan an intact 3-file qcow2 backing chain: base ← snap1 ← snap2.

    - ``success=True``
    - ``broken_files=[]``
    - ``paths`` contains all 3 files.
    """
    shell: SubprocessShell = test_vm["shell"]
    tmpdir: Path = test_vm["tmpdir"]

    # Create chain: base.qcow2 → snap1.qcow2 → snap2.qcow2
    chain_paths = _create_qcow2_chain(
        shell,
        tmpdir,
        ["base.qcow2", "snap1.qcow2", "snap2.qcow2"],
    )

    # Scan from the top (snap2).
    result = scan_backing_chain(shell, chain_paths[-1])

    assert result.success, f"Scan must succeed, got: {result.error}"
    assert result.broken_files == [], f"Expected no broken files, got: {result.broken_files}"
    assert len(result.paths) == 3, (
        f"Expected 3 paths in chain, got {len(result.paths)}: {result.paths}"
    )
    for p in chain_paths:
        assert str(p) in result.paths, f"Path {p} not found in scan result: {result.paths}"


# ──────────────────────────────────────────────────────────────────────
# Test 2: Broken chain (missing file) — broken_files is non-empty
# ──────────────────────────────────────────────────────────────────────


def test_scan_backing_chain_broken_missing_file(test_vm):
    """Scan a chain where one intermediate file has been deleted.

    After deleting ``snap1.qcow2``, ``scan_backing_chain()`` must report
    ``broken_files`` containing either the missing file or the file that
    depends on it.
    """
    shell: SubprocessShell = test_vm["shell"]
    tmpdir: Path = test_vm["tmpdir"]

    # Create chain: base.qcow2 → snap1.qcow2 → snap2.qcow2
    chain_paths = _create_qcow2_chain(
        shell,
        tmpdir,
        ["base.qcow2", "snap1.qcow2", "snap2.qcow2"],
    )

    # Delete the intermediate file to break the chain.
    snap1_path = chain_paths[1]
    snap1_path.unlink()

    # Scan from the top.
    result = scan_backing_chain(shell, chain_paths[-1])

    snap1_str = str(snap1_path)
    snap2_str = str(chain_paths[-1])

    # When a backing file is missing, qemu-img info --backing-chain may
    # fail entirely (exit code 1, no JSON output) because it cannot open
    # the chain.  In that case, success=False is a valid detection of the
    # broken chain — the error message mentions the missing file.
    if not result.success:
        # Scan command failed — the error should reference the missing file.
        assert snap1_str in (result.error or "") or "No such file" in (result.error or ""), (
            f"Expected error to mention missing file {snap1_str}. error={result.error}"
        )
    else:
        # Scan succeeded but found broken files.
        broken_str_set = {str(b) for b in result.broken_files}
        missing_referenced = snap1_str in broken_str_set or snap2_str in broken_str_set
        assert missing_referenced, (
            f"Expected broken_files to reference missing file {snap1_str} "
            f"or its dependent {snap2_str}.  Got: {result.broken_files}"
        )


# ──────────────────────────────────────────────────────────────────────
# Test 3: Non-qcow2 file in chain — detected by scan_backing_chain
# ──────────────────────────────────────────────────────────────────────


def test_scan_backing_chain_non_qcow2_detected(test_vm):
    """A non-qcow2 file in the backing chain is flagged in ``broken_files``.

    Creates: base.qcow2 → raw_intermediate.raw → top.qcow2
    (where top.qcow2 has backing raw_intermediate.raw).
    The scan should detect that raw_intermediate.raw is not qcow2.
    """
    shell: SubprocessShell = test_vm["shell"]
    tmpdir: Path = test_vm["tmpdir"]

    # Create base qcow2.
    base_path = tmpdir / "base.qcow2"
    r = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(base_path), "64K"],
        timeout=30,
    )
    assert r.success, f"Failed to create base: {r.error}"

    # Create a raw intermediate file (non-qcow2).
    raw_path = _create_raw_file(shell, tmpdir, "raw_intermediate.raw")

    # Create a top qcow2 with backing pointing to the raw file.
    # qemu-img create -b with a non-qcow2 backing is unusual but valid
    # for testing the detection.
    top_path = tmpdir / "top.qcow2"
    r = shell.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(raw_path),
            "-F",
            "raw",
            str(top_path),
            "64K",
        ],
        timeout=30,
    )
    if not r.success:
        # Some qemu-img versions may reject a non-qcow2 backing.
        # In that case, we create a chain where qemu-img info reports
        # the raw format.
        # Alternative: create a valid qcow2 chain then overwrite one
        # file's format metadata.  For simplicity, if this is not
        # supported, skip.
        pytest.skip(f"qemu-img does not support non-qcow2 backing: {r.error}")

    # Scan from the top.
    result = scan_backing_chain(shell, top_path)

    # The scan should detect the non-qcow2 format.
    if result.success:
        # When scan succeeds, broken_files should contain the raw file.
        broken_str_set = {str(b) for b in result.broken_files}
        raw_str = str(raw_path)
        assert raw_str in broken_str_set or len(result.broken_files) >= 1, (
            f"Expected non-qcow2 file {raw_str} in broken_files. Got: {result.broken_files}"
        )
    else:
        # If the scan command itself fails due to non-qcow2 in chain,
        # that's also acceptable — the error is detected.
        assert result.error is not None, "Failed scan must have an error message"


# ──────────────────────────────────────────────────────────────────────
# Test 4: Single-file chain (no backing) — scan returns success
# ──────────────────────────────────────────────────────────────────────


def test_scan_backing_chain_single_file(test_vm):
    """A standalone qcow2 with no backing file returns success with 1 path."""
    shell: SubprocessShell = test_vm["shell"]
    tmpdir: Path = test_vm["tmpdir"]

    standalone = tmpdir / "standalone.qcow2"
    r = shell.run(
        ["qemu-img", "create", "-f", "qcow2", str(standalone), "64K"],
        timeout=30,
    )
    assert r.success, f"Failed to create standalone qcow2: {r.error}"

    result = scan_backing_chain(shell, standalone)

    assert result.success, f"Scan of standalone must succeed: {result.error}"
    assert result.broken_files == [], (
        f"Standalone qcow2 must have no broken files: {result.broken_files}"
    )
    assert len(result.paths) == 1, f"Expected 1 path, got {len(result.paths)}: {result.paths}"
    assert str(standalone) in result.paths


# ──────────────────────────────────────────────────────────────────────
# Test 5: Non-existent entry path — scan returns failure
# ──────────────────────────────────────────────────────────────────────


def test_scan_backing_chain_nonexistent_entry(test_vm):
    """Scanning a non-existent file returns ``success=False``."""
    shell: SubprocessShell = test_vm["shell"]
    tmpdir: Path = test_vm["tmpdir"]

    nonexistent = tmpdir / "does_not_exist.qcow2"

    result = scan_backing_chain(shell, nonexistent)

    assert not result.success, (
        f"Scan of non-existent file must fail. success={result.success}, error={result.error}"
    )
    assert result.error is not None, "Error message must be present for failed scan"
