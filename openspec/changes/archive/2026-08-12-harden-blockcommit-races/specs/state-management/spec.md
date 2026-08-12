# State Management — delta

## ADDED Requirements

### Requirement: Commit intent journal persistence

`JsonStateManager` SHALL persist the commit intent journal (spec: `commit-intent-journal`)
under the top-level key `commit_in_progress` of the per-VM state file as a list of objects
with keys `disk`, `snapshots`, `base`, `started_ts`. Writes SHALL go through the existing
atomic tmp-file + `os.replace` path used for all state mutations, and the journal SHALL be
written in the same atomic save as any other state mutation of that call. State files lacking
the key SHALL load as an empty journal. `InMemoryStateManager` SHALL implement the same
`set_commit_in_progress` / `get_commit_in_progress` / `clear_commit_in_progress` semantics
in memory.

#### Scenario: Journal round-trip through JSON state

- **WHEN** `set_commit_in_progress("vm1", "vda", ["s1"], "/data/img.qcow2", "20260812T150126")` is called on `JsonStateManager`
- **AND** the state file is re-read by a fresh manager instance
- **THEN** `get_commit_in_progress("vm1")` returns the identical `CommitIntent`

#### Scenario: Journal write is atomic with other state

- **WHEN** a state save includes a journal update
- **THEN** the file is written via tmp + `os.replace` and a concurrent reader never observes a partial document

#### Scenario: Legacy state file loads cleanly

- **WHEN** a state file written before this feature is loaded
- **THEN** `get_commit_in_progress` returns an empty list and all pre-existing fields are unaffected
