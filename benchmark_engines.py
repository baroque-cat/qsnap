"""Benchmark: compare FULL backup speed across engines and compression modes.

Creates a disposable 4G qcow2 disk, writes 50 MB of patterned data,
then runs 6 FULL backups (2 engines x 3 compression modes) and
generates a bar chart.

Usage:
    poetry run python benchmark_engines.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from qsnap.models.config import TargetConfig
from qsnap.models.results import SnapshotInfo
from qsnap.modules.backup.bitmap import BitmapBackupProvider
from qsnap.shell.subprocess_shell import SubprocessShell
from qsnap.utils.nbd import is_libvirt_new_enough, is_vm_running
from qsnap.utils.nbd_client import LibnbdClient

logging.basicConfig(level=logging.WARNING)

VM_NAME = "qsnap-bench-vm"
DATA_MB = 50
DISK_SIZE = "4G"
TMP_ROOT = "/var/tmp"


def _write_data(shell: SubprocessShell, disk_path: Path, size_mb: int) -> bool:
    size_bytes = size_mb * 1024 * 1024
    result = shell.run(
        ["qemu-io", "-c", f"write -P 0xAA 0 {size_bytes}", str(disk_path)],
        timeout=120,
        check=True,
    )
    return result.success


def _qemu_img_info(shell: SubprocessShell, path: Path) -> dict | None:
    result = shell.run(
        ["qemu-img", "info", "--force-share", "--output=json", str(path)],
        timeout=30,
    )
    if not result.success:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _get_actual_size(shell: SubprocessShell, path: Path) -> int:
    info = _qemu_img_info(shell, path)
    if info is None:
        return -1
    return int(info.get("actual-size", 0))


def _cleanup_checkpoints(shell: SubprocessShell, vm_name: str) -> None:
    result = shell.run(
        ["virsh", "checkpoint-list", "--name", "--domain", vm_name],
        timeout=30,
    )
    if not result.success:
        return
    for line in result.stdout.strip().splitlines():
        cp = line.strip()
        if cp and cp.startswith("qsnap-"):
            shell.run(
                ["virsh", "checkpoint-delete", "--domain", vm_name, cp],
                timeout=30,
            )


def main() -> None:
    shell = SubprocessShell()
    tmpdir = Path(tempfile.mkdtemp(prefix="qsnap-bench-", dir=TMP_ROOT))
    base_image = tmpdir / f"{VM_NAME}.qcow2"
    target_dir = tmpdir / "backup"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create disk.
    shell.run(["qemu-img", "create", "-f", "qcow2", str(base_image), DISK_SIZE], timeout=30)
    _write_data(shell, base_image, DATA_MB)

    # Define VM.
    domain_type = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "qemu"
    xml = (
        f'<domain type="{domain_type}">\n'
        f"  <name>{VM_NAME}</name>\n"
        f"  <memory unit='KiB'>262144</memory>\n"
        f"  <vcpu placement='static'>1</vcpu>\n"
        f"  <os>\n"
        f"    <type arch='x86_64' machine='pc'>hvm</type>\n"
        f'    <boot dev="hd"/>\n'
        f"  </os>\n"
        f"  <devices>\n"
        f'    <disk type="file" device="disk">\n'
        f'      <driver name="qemu" type="qcow2"/>\n'
        f'      <source file="{base_image}"/>\n'
        f'      <target dev="vda" bus="virtio"/>\n'
        f"    </disk>\n"
        f"  </devices>\n"
        f"</domain>\n"
    )
    xml_path = tmpdir / f"{VM_NAME}.xml"
    xml_path.write_text(xml)
    shell.run(["virsh", "define", str(xml_path)], timeout=30)

    try:
        shell.run(["virsh", "start", VM_NAME], timeout=30)
        time.sleep(2)
        assert is_vm_running(shell, VM_NAME), "VM did not start"
        assert is_libvirt_new_enough(shell), "libvirt too old"

        engines = [
            ("qemu-img-convert", BitmapBackupProvider(shell)),
            ("libnbd", BitmapBackupProvider(shell, nbd=LibnbdClient())),
        ]
        compressions = [("none", False, "zstd"), ("zstd", True, "zstd"), ("zlib", True, "zlib")]

        results: list[dict] = []
        for engine_name, provider in engines:
            for comp_name, compress, comp_type in compressions:
                _cleanup_checkpoints(shell, VM_NAME)
                time.sleep(1.1)
                label = f"{engine_name}/{comp_name}"
                print(f"  Running {label}...", flush=True)
                t0 = time.monotonic()
                r = provider.create_full_backup(
                    VM_NAME,
                    SnapshotInfo(
                        name=f"{VM_NAME}.{engine_name}-{comp_name}",
                        path=base_image,
                        timestamp=datetime.now(),
                        allocation=0,
                    ),
                    TargetConfig(path=target_dir, incremental=True, compress=compress, verify="off"),
                    compress=compress,
                    compression_type=comp_type,
                    bucket_level="monthly",
                    full_transfer_engine=engine_name,
                )
                elapsed = time.monotonic() - t0
                if not r.success:
                    print(f"    FAILED: {r.error}")
                    results.append(
                        {"engine": engine_name, "compression": comp_name, "time_s": 0, "actual_bytes": 0, "success": False}
                    )
                    continue
                actual = _get_actual_size(shell, r.target_path)
                throughput = (DATA_MB / elapsed) if elapsed > 0 else 0
                print(f"    time={elapsed:.1f}s  actual={actual}  throughput={throughput:.1f} MB/s")
                results.append(
                    {
                        "engine": engine_name,
                        "compression": comp_name,
                        "time_s": elapsed,
                        "actual_bytes": actual,
                        "throughput_mbs": throughput,
                        "success": True,
                    }
                )

        _cleanup_checkpoints(shell, VM_NAME)
    finally:
        shell.run(["virsh", "destroy", VM_NAME], timeout=30)
        _cleanup_checkpoints(shell, VM_NAME)
        shell.run(["virsh", "undefine", VM_NAME], timeout=30)
        shutil.rmtree(str(tmpdir), ignore_errors=True)

    # ── Generate chart ──
    engines_list = ["qemu-img-convert", "libnbd"]
    compressions_list = ["none", "zstd", "zlib"]
    x = np.arange(len(compressions_list))
    width = 0.35

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Chart 1: Throughput (MB/s)
    ax1 = axes[0]
    for i, engine in enumerate(engines_list):
        throughputs = [
            next(
                (r["throughput_mbs"] for r in results if r["engine"] == engine and r["compression"] == comp),
                0,
            )
            for comp in compressions_list
        ]
        bars = ax1.bar(x + i * width - width / 2, throughputs, width, label=engine)
        for bar, val in zip(bars, throughputs, strict=False):
            if val > 0:
                ax1.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 5,
                    f"{val:.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )
    ax1.set_xlabel("Compression Mode")
    ax1.set_ylabel("Throughput (MB/s)")
    ax1.set_title(f"FULL Backup Throughput ({DATA_MB} MB data, {DISK_SIZE} disk)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(compressions_list)
    ax1.legend()
    ax1.set_ylim(0, max(throughputs) * 1.2 if throughputs else 100)

    # Chart 2: Elapsed time (seconds, log scale)
    ax2 = axes[1]
    for i, engine in enumerate(engines_list):
        times = [
            next(
                (r["time_s"] for r in results if r["engine"] == engine and r["compression"] == comp),
                0,
            )
            for comp in compressions_list
        ]
        bars = ax2.bar(x + i * width - width / 2, times, width, label=engine)
        for bar, val in zip(bars, times, strict=False):
            if val > 0:
                ax2.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() * 1.05,
                    f"{val:.1f}s",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )
    ax2.set_xlabel("Compression Mode")
    ax2.set_ylabel("Elapsed Time (seconds)")
    ax2.set_title(f"FULL Backup Time ({DATA_MB} MB data, {DISK_SIZE} disk)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(compressions_list)
    ax2.legend()
    ax2.set_yscale("log")
    ax2.set_ylim(0.5, max(times) * 3 if times else 100)

    fig.suptitle("qsnap FULL Backup: Engine × Compression Comparison", fontsize=14, fontweight="bold")
    plt.tight_layout()

    output_path = "/home/openuser/vm/qsnap/benchmark_engines.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\nChart saved to {output_path}")

    # Also print a summary table.
    print("\n=== Summary ===")
    print(f"{'Engine':<20} {'Compression':<12} {'Time (s)':>10} {'Actual (B)':>12} {'MB/s':>10}")
    print("-" * 66)
    for r in results:
        if r["success"]:
            print(
                f"{r['engine']:<20} {r['compression']:<12} "
                f"{r['time_s']:>10.1f} {r['actual_bytes']:>12} {r['throughput_mbs']:>10.1f}"
            )
        else:
            print(f"{r['engine']:<20} {r['compression']:<12} {'FAILED':>10}")


if __name__ == "__main__":
    main()
