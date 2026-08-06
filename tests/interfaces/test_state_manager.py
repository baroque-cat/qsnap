"""Contract test: IStateManager ABC and concrete implementations."""

from __future__ import annotations

import pytest

from qsnap.interfaces.state import IStateManager
from qsnap.state.json_manager import JsonStateManager
from tests.mocks.mock_state import InMemoryStateManager


def test_istate_manager_is_abstract():
    """IStateManager is an ABC; cannot be instantiated directly.

    JsonStateManager is a subclass, and InMemoryStateManager instances pass
    ``isinstance`` against ``IStateManager``.
    """
    # IStateManager is an ABC with non-empty abstract methods.
    assert hasattr(IStateManager, "__abstractmethods__")
    assert len(IStateManager.__abstractmethods__) > 0

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IStateManager()  # type: ignore[abstract]

    # JsonStateManager is a subclass of IStateManager.
    assert issubclass(JsonStateManager, IStateManager)

    # An InMemoryStateManager instance is an IStateManager.
    assert isinstance(InMemoryStateManager(), IStateManager)


def test_istate_manager_deferred_operations_methods_exist():
    """IStateManager declares deferred blockcommit operations as abstract.

    The methods ``get_deferred_operations``, ``add_deferred_blockcommit``,
    and ``clear_deferred_operations`` must all be in
    ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_deferred_operations" in abstract_methods
    assert "add_deferred_blockcommit" in abstract_methods
    assert "clear_deferred_operations" in abstract_methods


def test_istate_manager_full_backup_methods_abstract():
    """IStateManager declares full-backup tracking methods as abstract.

    The methods ``get_last_full_backup`` and ``set_last_full_backup`` must
    be in ``IStateManager.__abstractmethods__`` so that every concrete
    implementation (e.g. ``JsonStateManager``, ``InMemoryStateManager``)
    is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_last_full_backup" in abstract_methods
    assert "set_last_full_backup" in abstract_methods


def test_istate_manager_update_deferred_warning_abstract(tmp_path):
    """IStateManager declares update_deferred_warning as abstract.

    The method ``update_deferred_warning`` must be in
    ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide it.  Both ``JsonStateManager``
    and ``InMemoryStateManager`` must implement it.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "update_deferred_warning" in abstract_methods

    # Both concrete implementations implement the method.
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()
    assert callable(json_mgr.update_deferred_warning)
    assert callable(inmemory_mgr.update_deferred_warning)


def test_istate_manager_multi_full_backup_methods_abstract():
    """IStateManager declares multi-FULL backup tracking methods as abstract.

    The methods ``get_full_backups``, ``record_full_backup``,
    ``record_incremental_dependency``, and ``get_incremental_dependencies``
    must be in ``IStateManager.__abstractmethods__`` so that every concrete
    implementation is forced to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_full_backups" in abstract_methods
    assert "record_full_backup" in abstract_methods
    assert "record_incremental_dependency" in abstract_methods
    assert "get_incremental_dependencies" in abstract_methods


def test_istate_manager_concrete_implementations_have_new_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement new abstract methods.

    Both concrete implementations must provide ``get_full_backups``,
    ``record_full_backup``, ``record_incremental_dependency``, and
    ``get_incremental_dependencies`` with correct signatures.
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.get_full_backups), f"{label} missing get_full_backups"
        assert callable(mgr.record_full_backup), f"{label} missing record_full_backup"
        assert callable(mgr.record_incremental_dependency), (
            f"{label} missing record_incremental_dependency"
        )
        assert callable(mgr.get_incremental_dependencies), (
            f"{label} missing get_incremental_dependencies"
        )


def test_istate_manager_new_methods_cause_typeerror_on_instantiation():
    """Instantiating a subclass missing the new abstract methods raises TypeError.

    A bare subclass that implements older abstract methods but not the four
    new ones (``get_full_backups``, ``record_full_backup``,
    ``record_incremental_dependency``, ``get_incremental_dependencies``)
    must raise ``TypeError`` because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_full_backups" in abstract_methods

    # A subclass that is missing the new methods must fail to instantiate.
    class _MissingNewMethods(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...

    with pytest.raises(TypeError):
        _MissingNewMethods()


def test_json_state_manager_passes_isinstance_after_corruption_recovery(
    tmp_path,
) -> None:
    """JsonStateManager remains an IStateManager after corruption recovery.

    Create a ``JsonStateManager``, verify ``isinstance(manager,
    IStateManager)`` is True.  Then trigger corruption recovery (write
    a corrupt file, load), and verify ``isinstance`` is still True.
    """

    manager = JsonStateManager(state_dir=tmp_path)

    # Before corruption — passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should be an IStateManager before corruption"
    )

    # Write corrupt file and trigger recovery.
    state_file = tmp_path / "testvm_corrupt.json"
    state_file.write_text("{ this is not valid json", encoding="utf-8")

    # Loading triggers corruption recovery (rename + empty state).
    last_alloc = manager.get_last_allocation("testvm_corrupt", "vda")
    assert last_alloc is None, "Corrupt state should return None after recovery"

    # After corruption recovery — still passes isinstance.
    assert isinstance(manager, IStateManager), (
        "JsonStateManager should still be an IStateManager after corruption recovery"
    )


# ── per-target backup allocation contract ──────────────────────────────────


def test_istate_manager_backup_allocation_methods_abstract():
    """get_last_backup_allocation and set_last_backup_allocation are abstract on IStateManager.

    A subclass missing only these two new methods must fail to instantiate
    with TypeError because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "get_last_backup_allocation" in abstract_methods
    assert "set_last_backup_allocation" in abstract_methods
    assert "clear_last_backup_allocation" in abstract_methods
    assert "remove_all_incremental_dependencies" in abstract_methods

    # A subclass that implements everything EXCEPT the backup allocation
    # methods and the bulk dependency removal method must fail to instantiate.
    class _MissingBackupAlloc(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...

    with pytest.raises(TypeError):
        _MissingBackupAlloc()


def test_inmemory_manager_implements_backup_allocation():
    """InMemoryStateManager implements get_last_backup_allocation and set_last_backup_allocation."""
    mgr = InMemoryStateManager()

    # Verify methods exist and are callable.
    assert callable(mgr.get_last_backup_allocation)
    assert callable(mgr.set_last_backup_allocation)

    # Verify they work correctly.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") == 5000

    # Missing target returns None.
    assert mgr.get_last_backup_allocation("/nonexistent", "vda") is None


def test_json_manager_implements_backup_allocation(tmp_path):
    """JsonStateManager implements get_last_backup_allocation and set_last_backup_allocation."""
    mgr = JsonStateManager(state_dir=tmp_path)

    # Verify methods exist and are callable.
    assert callable(mgr.get_last_backup_allocation)
    assert callable(mgr.set_last_backup_allocation)

    # Verify they work correctly.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") == 5000

    # Missing target returns None.
    assert mgr.get_last_backup_allocation("/nonexistent", "vda") is None


# ── clear_last_backup_allocation contract ───────────────────────────────


def test_istate_manager_clear_allocation_abstract():
    """clear_last_backup_allocation is abstract on IStateManager.

    A subclass missing only this method must fail to instantiate with
    TypeError because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "clear_last_backup_allocation" in abstract_methods

    # A subclass that implements everything EXCEPT clear_last_backup_allocation
    # must fail to instantiate.
    class _MissingClearBackupAlloc(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...

    with pytest.raises(TypeError):
        _MissingClearBackupAlloc()


def test_istate_manager_remove_all_deps_abstract():
    """remove_all_incremental_dependencies is abstract on IStateManager.

    A subclass missing only this method must fail to instantiate with
    TypeError because the ABC enforces all abstract methods.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "remove_all_incremental_dependencies" in abstract_methods

    # A subclass that implements everything EXCEPT
    # remove_all_incremental_dependencies must fail to instantiate.
    class _MissingRemoveAllDeps(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...

    with pytest.raises(TypeError):
        _MissingRemoveAllDeps()


def test_inmemory_implements_clear_allocation():
    """InMemoryStateManager implements clear_last_backup_allocation, returns bool."""
    mgr = InMemoryStateManager()

    # Verify method exists and is callable.
    assert callable(mgr.clear_last_backup_allocation)

    # Verify it works correctly — clearing an existing entry returns True.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    result = mgr.clear_last_backup_allocation("/mnt/backup/test", "vda")
    assert isinstance(result, bool)
    assert result is True

    # Verify the entry was actually removed.
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") is None

    # Clearing a non-existent entry returns False.
    result2 = mgr.clear_last_backup_allocation("/nonexistent", "vda")
    assert isinstance(result2, bool)
    assert result2 is False


def test_json_implements_clear_allocation(tmp_path):
    """JsonStateManager implements clear_last_backup_allocation, returns bool."""
    mgr = JsonStateManager(state_dir=tmp_path)

    # Verify method exists and is callable.
    assert callable(mgr.clear_last_backup_allocation)

    # Verify it works correctly — clearing an existing entry returns True.
    mgr.set_last_backup_allocation("/mnt/backup/test", "vda", 5000)
    result = mgr.clear_last_backup_allocation("/mnt/backup/test", "vda")
    assert isinstance(result, bool)
    assert result is True

    # Verify the entry was actually removed.
    assert mgr.get_last_backup_allocation("/mnt/backup/test", "vda") is None

    # Clearing a non-existent entry returns False.
    result2 = mgr.clear_last_backup_allocation("/nonexistent", "vda")
    assert isinstance(result2, bool)
    assert result2 is False


def test_inmemory_implements_remove_all_deps():
    """InMemoryStateManager implements remove_all_incremental_dependencies, returns int."""
    mgr = InMemoryStateManager()

    # Verify method exists and is callable.
    assert callable(mgr.remove_all_incremental_dependencies)

    # Record some dependencies and verify remove_all returns the correct count.
    mgr.record_incremental_dependency("/target", "inc1", "full1")
    mgr.record_incremental_dependency("/target", "inc2", "full1")
    mgr.record_incremental_dependency("/target", "inc3", "full1")

    result = mgr.remove_all_incremental_dependencies("/target", "full1")
    assert isinstance(result, int)
    assert result == 3

    # Verify dependencies were actually removed.
    remaining = mgr.get_incremental_dependencies("/target", "full1")
    assert remaining == []

    # Removing all deps for a non-existent full backup returns 0.
    result2 = mgr.remove_all_incremental_dependencies("/target", "nonexistent")
    assert isinstance(result2, int)
    assert result2 == 0

    # Removing all deps for a non-existent target returns 0.
    result3 = mgr.remove_all_incremental_dependencies("/nonexistent_tgt", "full1")
    assert isinstance(result3, int)
    assert result3 == 0


# ── per-target state independence contract ────────────────────────────


def test_inmemory_per_target_state_independent():
    """Per-target backup allocation is independent.

    Setting a baseline for one target does not affect baselines
    for other targets (independent-target-onchange spec).
    """
    mgr = InMemoryStateManager()

    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 1000)
    mgr.set_last_backup_allocation("/mnt/backup/targetB", "vda", 2000)

    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 1000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000

    # Overwrite targetA — targetB is unaffected.
    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 3000)
    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 3000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000


def test_json_per_target_state_independent(tmp_path):
    """JsonStateManager per-target backup allocation is independent.

    Verifies that persisted state for one target does not leak into
    another target's baseline (independent-target-onchange spec).
    """
    mgr = JsonStateManager(state_dir=tmp_path)

    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 1000)
    mgr.set_last_backup_allocation("/mnt/backup/targetB", "vda", 2000)

    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 1000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000

    # Overwrite targetA — targetB is unaffected.
    mgr.set_last_backup_allocation("/mnt/backup/targetA", "vda", 3000)
    assert mgr.get_last_backup_allocation("/mnt/backup/targetA", "vda") == 3000
    assert mgr.get_last_backup_allocation("/mnt/backup/targetB", "vda") == 2000


def test_json_implements_remove_all_deps(tmp_path):
    """JsonStateManager implements remove_all_incremental_dependencies, returns int."""
    manager = JsonStateManager(state_dir=tmp_path)

    # Verify method exists and is callable.
    assert callable(manager.remove_all_incremental_dependencies)

    # Record some dependencies and verify remove_all returns the correct count.
    manager.record_incremental_dependency("/target", "inc1", "full1")
    manager.record_incremental_dependency("/target", "inc2", "full1")
    manager.record_incremental_dependency("/target", "inc3", "full1")

    result = manager.remove_all_incremental_dependencies("/target", "full1")
    assert isinstance(result, int)
    assert result == 3

    # Verify dependencies were actually removed.
    remaining = manager.get_incremental_dependencies("/target", "full1")
    assert remaining == []

    # Removing all deps for a non-existent full backup returns 0.
    result2 = manager.remove_all_incremental_dependencies("/target", "nonexistent")
    assert isinstance(result2, int)
    assert result2 == 0

    # Removing all deps for a non-existent target returns 0.
    result3 = manager.remove_all_incremental_dependencies("/nonexistent_tgt", "full1")
    assert isinstance(result3, int)
    assert result3 == 0


# ── reset_vm_state / reset_target_state contract ──────────────────────────


def test_istate_manager_reset_methods_abstract():
    """reset_vm_state, reset_target_state, and per-disk resets are abstract on IStateManager.

    A subclass missing the VM-level reset methods must fail to instantiate
    with TypeError because the ABC enforces all abstract methods.  The
    per-disk counterparts (``reset_vm_disk_state``, ``reset_target_disk_state``)
    are also verified as abstract, completing the full reset-method contract.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "reset_vm_state" in abstract_methods
    assert "reset_target_state" in abstract_methods
    assert "reset_vm_disk_state" in abstract_methods
    assert "reset_target_disk_state" in abstract_methods

    # A subclass that implements everything EXCEPT the VM-level reset
    # methods must fail to instantiate.
    class _MissingResetMethods(IStateManager):
        def get_last_allocation(self, vm_name): ...
        def set_last_allocation(self, vm_name, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...

    with pytest.raises(TypeError):
        _MissingResetMethods()


def test_istate_manager_concrete_implementations_have_reset_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement reset_vm_state and reset_target_state."""
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.reset_vm_state), f"{label} missing reset_vm_state"
        assert callable(mgr.reset_target_state), f"{label} missing reset_target_state"


# ── per-disk state reset contract (design D4) ────────────────────────────


def test_istate_manager_per_disk_reset_methods_abstract():
    """reset_vm_disk_state and reset_target_disk_state are abstract on IStateManager.

    The per-disk reset methods declared by ``IStateManager`` for the
    fix-per-disk-isolation change (design D4) MUST be present in
    ``__abstractmethods__`` so that every concrete implementation is forced
    to provide them.
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "reset_vm_disk_state" in abstract_methods
    assert "reset_target_disk_state" in abstract_methods


def test_concrete_implementations_have_per_disk_reset_methods(tmp_path):
    """JsonStateManager and InMemoryStateManager implement the per-disk reset methods.

    Both concrete managers provide callable ``reset_vm_disk_state`` and
    ``reset_target_disk_state`` implementations matching the signatures
    declared in ``IStateManager``.
    """
    json_mgr = JsonStateManager(state_dir=tmp_path)
    inmemory_mgr = InMemoryStateManager()

    for mgr, label in [
        (json_mgr, "JsonStateManager"),
        (inmemory_mgr, "InMemoryStateManager"),
    ]:
        assert callable(mgr.reset_vm_disk_state), f"{label} missing reset_vm_disk_state"
        assert callable(mgr.reset_target_disk_state), f"{label} missing reset_target_disk_state"


def test_missing_per_disk_reset_fails_instantiation():
    """A subclass missing per-disk reset methods raises TypeError on instantiation.

    When a concrete subclass of ``IStateManager`` provides every abstract
    method EXCEPT ``reset_vm_disk_state`` and ``reset_target_disk_state``,
    instantiation MUST raise ``TypeError`` because the ABC enforces all
    abstract methods.  This guarantees that no future implementation can
    silently omit the per-disk reset contract (design D4).
    """
    abstract_methods = IStateManager.__abstractmethods__
    assert "reset_vm_disk_state" in abstract_methods
    assert "reset_target_disk_state" in abstract_methods

    # A subclass that implements everything EXCEPT the two per-disk reset
    # methods — including reset_vm_state and reset_target_state.
    class _MissingPerDiskReset(IStateManager):
        def get_last_allocation(self, vm_name, disk): ...
        def set_last_allocation(self, vm_name, disk, alloc): ...
        def record_snapshot(self, vm_name, info): ...
        def remove_snapshot(self, vm_name, snapshot_name): ...
        def get_snapshots(self, vm_name): ...
        def get_deferred_operations(self, vm_name): ...
        def add_deferred_blockcommit(self, vm_name, disk, snapshots, reason): ...
        def clear_deferred_operations(self, vm_name): ...
        def update_deferred_warning(self, vm_name, index, timestamp): ...
        def get_last_full_backup(self, target_path): ...
        def set_last_full_backup(self, target_path, name, timestamp, disk): ...
        def get_full_backups(self, target_path): ...
        def record_full_backup(self, target_path, name, timestamp, disk): ...
        def record_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_incremental_dependencies(self, target_path, full_name): ...
        def remove_full_backup(self, target_path, name): ...
        def remove_incremental_dependency(self, target_path, incremental_name, full_name): ...
        def get_last_backup_allocation(self, target_path, disk): ...
        def set_last_backup_allocation(self, target_path, disk, alloc): ...
        def clear_last_backup_allocation(self, target_path, disk): ...
        def remove_all_incremental_dependencies(self, target_path, full_name): ...
        def reset_vm_state(self, vm_name): ...
        def reset_target_state(self, target_path): ...

    with pytest.raises(TypeError):
        _MissingPerDiskReset()


# ── deferred blockcommit enospc contract ──────────────────────────────────


def test_inmemory_add_deferred_blockcommit_accepts_enospc_reason():
    """InMemoryStateManager.add_deferred_blockcommit accepts reason='enospc'.

    The entry is stored and round-trips through get_deferred_operations
    with reason='enospc' intact (deferred-operations requirement:
    "Add deferred blockcommit with enospc reason").
    """
    mgr = InMemoryStateManager()
    mgr.add_deferred_blockcommit("testvm", "vda", ["snap1.qcow2"], "enospc")

    deferred = mgr.get_deferred_operations("testvm")
    assert len(deferred) == 1
    entry = deferred[0]
    assert entry.reason == "enospc"
    assert entry.disk == "vda"
    assert entry.snapshots == ["snap1.qcow2"]
    assert entry.last_warned_at is None

    # Round-trip: reading again returns the same entry with reason intact.
    deferred_again = mgr.get_deferred_operations("testvm")
    assert len(deferred_again) == 1
    assert deferred_again[0].reason == "enospc"


def test_json_add_deferred_blockcommit_accepts_enospc_reason(tmp_path):
    """JsonStateManager.add_deferred_blockcommit accepts reason='enospc'.

    The entry persists across a state round-trip: a fresh manager reading
    the same state directory returns the entry with reason='enospc' intact
    (deferred-operations requirement + state-recovery persistence).
    """
    mgr = JsonStateManager(state_dir=tmp_path)
    mgr.add_deferred_blockcommit("testvm", "vda", ["snap1.qcow2"], "enospc")

    # Round-trip through a fresh manager reading the persisted state file.
    mgr2 = JsonStateManager(state_dir=tmp_path)
    deferred = mgr2.get_deferred_operations("testvm")
    assert len(deferred) == 1
    entry = deferred[0]
    assert entry.reason == "enospc"
    assert entry.disk == "vda"
    assert entry.snapshots == ["snap1.qcow2"]
    assert entry.last_warned_at is None
