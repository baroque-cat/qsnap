# state-consistency-check — Delta Spec

## ADDED Requirements

### Requirement: check_state uses shared detection methods from Core

`Core.check_state()` SHALL delegate phantom snapshot detection, phantom FULL detection, stale dependency detection, and broken chain detection to the same shared private detector methods used by `Core.reconcile()`: `_detect_phantom_snapshots(vm)`, `_detect_phantom_fulls(vm)`, `_detect_stale_deps(vm)`, and `_detect_broken_chains(vm)`. `check_state()` SHALL consume the returned data to format its `StateCheckResult` output (building status strings like `"stale_snapshots"`, `"stale_fulls"`, `"stale_deps"`, `"broken_chains"`). The detection logic SHALL be identical between `check_state()` and `reconcile()` — only the downstream action differs (reporting vs. repair).

#### Scenario: check_state phantom snapshot detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with phantom snapshots
- **THEN** `_detect_phantom_snapshots(vm)` is called and returns a list of phantom `SnapshotInfo`
- **AND** `check_state()` formats them as `"stale_snapshots"` status part with file paths

#### Scenario: check_state phantom FULL detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with phantom FULLs
- **THEN** `_detect_phantom_fulls(vm)` is called and returns a list
- **AND** `check_state()` formats them as `"stale_fulls"` status part

#### Scenario: check_state stale dependency detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with stale deps
- **THEN** `_detect_stale_deps(vm)` is called and returns a list
- **AND** `check_state()` formats them as `"stale_deps"` status part

#### Scenario: check_state broken chain detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with broken backup chains
- **THEN** `_detect_broken_chains(vm)` is called and returns a list
- **AND** `check_state()` formats them as `"broken_chains"` status part

#### Scenario: check_state and reconcile produce identical detection results

- **WHEN** both `check_state(vm_filter)` and `reconcile(vm_filter)` are called on the same VM state
- **THEN** the phantom snapshots, FULLs, stale deps, and broken chains detected are identical
- **AND** only the downstream actions differ (reporting vs. repair)
