## ADDED Requirements

### Requirement: IStateManager deferred operations methods
`IStateManager` SHALL provide `get_deferred_operations(vm_name: str) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name: str, snapshots: list[str], reason: str)`, and `clear_deferred_operations(vm_name: str)`. `DeferredBlockcommit` SHALL be a frozen dataclass with fields `snapshots: list[str]`, `reason: str` (`"apparmor"` | `"selinux"`), `since: datetime`.

#### Scenario: Add and retrieve deferred operations
- **WHEN** `add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")` is called
- **THEN** `get_deferred_operations("vm1")` returns one `DeferredBlockcommit`

#### Scenario: Clear deferred operations
- **WHEN** `clear_deferred_operations("vm1")` is called
- **THEN** `get_deferred_operations("vm1")` returns an empty list

#### Scenario: Deferred operations persisted to JSON
- **WHEN** `JsonStateManager` writes deferred operations for a VM
- **THEN** they are stored in the VM's state JSON file under the `deferred_operations` key
- **THEN** they are loaded correctly on the next qsnap run

#### Scenario: No deferred operations — empty list
- **WHEN** `get_deferred_operations("vm_new")` is called for a VM with no deferred state
- **THEN** the method returns an empty list
