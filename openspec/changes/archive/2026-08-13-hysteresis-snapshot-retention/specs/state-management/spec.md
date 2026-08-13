# State Management (DELTA)

## ADDED Requirements

### Requirement: collapse_in_progress phase key

Per-VM state (`{vm}.json`) SHALL support an additive key `collapse_in_progress`: a list of
disk names currently in the hysteresis collapse phase. A missing key SHALL be treated as an
empty list (no migration required; old code ignores unknown keys). `IStateManager` SHALL
expose additive methods to set/clear the marker per disk and to read the list; concrete
`JsonStateManager` SHALL persist it atomically (tmp + replace) like all other state writes.
Dry-run SHALL NOT write the marker. `reset_vm_state` SHALL clear the key for the VM;
`reset_vm_disk_state` SHALL remove one disk from the list. Mock implementations SHALL mirror
the interface.

#### Scenario: Missing key reads as empty

- **WHEN** a state file has no `collapse_in_progress` key
- **THEN** readers observe an empty phase list

#### Scenario: Marker survives atomic write

- **WHEN** the marker is set for `vda` and the state file is reloaded
- **THEN** `vda` is present in `collapse_in_progress`

#### Scenario: Reset clears the marker

- **WHEN** `reset_vm_state("vm1")` runs while `collapse_in_progress = ["vda"]`
- **THEN** the key is cleared for `vm1`

#### Scenario: Old code tolerates the new key

- **WHEN** a pre-change qsnap binary reads a state file containing `collapse_in_progress`
- **THEN** loading succeeds and the key is ignored
