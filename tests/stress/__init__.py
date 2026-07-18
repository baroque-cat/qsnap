"""Stress tests — long chains, concurrent access, disk-full scenarios.

Marked with ``@pytest.mark.stress`` and excluded from normal test runs via
``-m "not stress"``.  These tests require a libvirt environment with
disposable test VMs and are designed to exercise edge cases that unit
tests cannot reach (chain depth limits, lockfile contention, etc.).
"""
