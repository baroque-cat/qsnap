# state-reconciliation — Delta Spec

## ADDED Requirements

### Requirement: Reconcile uses shared detection methods from Core

`Core.reconcile()` SHALL delegate phantom snapshot detection, phantom FULL detection, stale dependency detection, and broken chain detection to shared private detector methods on Core: `_detect_phantom_snapshots(vm)`, `_detect_phantom_fulls(vm)`, `_detect_stale_deps(vm)`, and `_detect_broken_chains(vm)`. These methods SHALL return pure data (lists of detected items) without performing any state mutation, logging, or dry-run gating. `reconcile()` SHALL consume the returned data to perform its repair actions (state mutation, XML refresh, file deletion, log warnings). The detector methods SHALL be shared with `Core.check_state()`, eliminating the current code duplication between the two methods. The detectors SHALL NOT receive pre-parsed XML data — `reconcile()` SHALL parse domain XML via `_parse_domain_xml_source_paths()` before calling detectors that need it, and pass the XML paths as parameters.

`_cross_reference_snapshots()` SHALL remain the exclusive triple-source matrix classifier (state/disk/XML → OK/phantom/stale-XML/orphan). `reconcile()` continues to use its own inline classification for the repair decision-making, but the raw data collection (file existence checks, dependency traversal) SHALL go through the shared detectors.

#### Scenario: Reconcile phantom snapshot detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with phantom snapshots
- **THEN** `_detect_phantom_snapshots(vm)` is called and returns a list of phantom `SnapshotInfo`
- **AND** `reconcile()` processes the returned list for state removal (optionally cross-referencing with `xml_paths`)

#### Scenario: Reconcile phantom FULL detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with phantom FULLs
- **THEN** `_detect_phantom_fulls(vm)` is called and returns a list of `(TargetConfig, FullBackupInfo)` tuples
- **AND** `reconcile()` processes the returned list for cascade cleanup

#### Scenario: Reconcile stale dependency detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with stale dependencies
- **THEN** `_detect_stale_deps(vm)` is called and returns a list of `(dep_name, full_name, TargetConfig)` tuples
- **AND** `reconcile()` processes the returned list for dependency removal

#### Scenario: Reconcile broken chain detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with broken backup chains
- **THEN** `_detect_broken_chains(vm)` is called and returns a list of backup names with broken chains
- **AND** `reconcile()` processes the returned list for CRITICAL logging (no auto-repair)

#### Scenario: Detectors return data only — no side effects

- **WHEN** any shared detector method is called
- **THEN** it does NOT modify `IStateManager`
- **AND** it does NOT delete files
- **AND** it does NOT log at WARNING or higher severity
- **AND** it does NOT check `self._dry_run`
