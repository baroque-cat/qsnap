## ADDED Requirements

### Requirement: Core.list_deferred() method

Core SHALL expose a `list_deferred(vm_filter=None)` method that retrieves deferred blockcommit operations from `IStateManager` for all configured VMs (or filtered VMs) and returns per-VM summaries. Each summary SHALL include: VM name, count of pending snapshots, reason, and age of the oldest deferred entry.

#### Scenario: list_deferred returns per-VM summaries

- **WHEN** `core.list_deferred()` is called
- **AND** two VMs have 3 and 5 deferred operations respectively
- **THEN** two summaries are returned with the correct counts and ages

#### Scenario: list_deferred with no deferred operations

- **WHEN** `core.list_deferred()` is called and no VM has any deferred operations
- **THEN** an empty list is returned

#### Scenario: list_deferred filtered by VM name

- **WHEN** `core.list_deferred(vm_filter="vm-home")` is called
- **THEN** only the summary for "vm-home" is returned
