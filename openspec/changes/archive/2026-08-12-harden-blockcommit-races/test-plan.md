# QA Strategy & Test Plan

**Change:** `harden-blockcommit-races` — harden the blockcommit path against distributed races (timeout ≠ failure, post-timeout reconciliation, intent journal, block-job protocol, fail-closed offline guard, heartbeat execution, configurable timeout, dynamic chain-walk bound, observability).

**Scope of this plan:** 12 delta spec files, `design.md` (D1–D9 + Risks), `proposal.md`, `TESTING.md` (authoritative paradigm), and the existing suites enumerated below. No production or test code is modified by this plan — it is the QA blueprint for the implementing agent.

**Paradigm conformance (TESTING.md):** test hierarchy mirrors production hierarchy; categories are unit / mock / contract / integration / stress / e2e with pytest markers; all ABC mocks (`MockShell` with `.expect().returns()`, `InMemoryStateManager`, `MockVMModuleFactory`) implement the new abstract methods in the same change; `unittest.mock` is used only for spying/datetime; contract tests parametrize over ALL implementations; shared fixtures (`make_vm_config`, `make_target`, `make_global_config`, `mock_shell`, `mock_state`, `mock_factory`, `frozen_clock`) are extended, never duplicated.

**Scenario total:** **91** `#### Scenario:` blocks across the 12 delta specs (verified by counting: blockcommit-recovery 4, blockjob-protocol 11, commit-intent-journal 12, commit-observability 5, commit-reconciliation 11, config-model 4, core-orchestrator 8, deferred-operations 11, lifecycle-manager 14, result-types 3, shell-abstraction 5, state-management 3). Every one appears exactly once in the Coverage Map.

## Coverage Map

| Spec Capability | Requirement | Scenario | Test File | Test Name | Group |
|---|---|---|---|---|---|
| commit-reconciliation | Three-valued commit outcome classification | Timeout outcome is classified unknown, not failure | tests/core/test_pipeline.py | test_unknown_outcome_not_treated_as_failure | core-orchestration-unit |
| commit-reconciliation | Three-valued commit outcome classification | Definitive failure still aborts the VM | tests/core/test_pipeline.py | test_definitive_commit_failure_aborts_vm | core-orchestration-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Late success detected after client timeout | tests/core/test_pipeline.py | test_reconcile_late_success_after_timeout | core-orchestration-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Job still active after timeout | tests/core/test_pipeline.py | test_reconcile_job_active_after_timeout | core-orchestration-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Dead job with no effect | tests/core/test_pipeline.py | test_reconcile_dead_job_no_effect | core-orchestration-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Contradictory evidence is inconclusive | tests/core/test_pipeline.py | test_reconcile_contradictory_evidence_inconclusive | core-orchestration-unit |
| commit-reconciliation | Post-unknown reconciliation protocol | Probe failure is inconclusive | tests/core/test_pipeline.py | test_reconcile_probe_failure_inconclusive | core-orchestration-unit |
| commit-reconciliation | Late-success state convergence | State synced after late success | tests/core/test_pipeline.py | test_late_success_converges_state_continues | core-orchestration-unit |
| commit-reconciliation | Job-active and inconclusive outcomes fail closed | Active job defers the disk without failing the VM | tests/core/test_pipeline.py | test_job_active_defers_disk_vm_not_failed | core-orchestration-unit |
| commit-reconciliation | Job-active and inconclusive outcomes fail closed | Inconclusive reconciliation defers fail-closed | tests/core/test_pipeline.py | test_inconclusive_defers_fail_closed | core-orchestration-unit |
| commit-reconciliation | True failure after reconciliation aborts with diagnostics | Dead job aborts the VM with a hint | tests/core/test_pipeline.py | test_reconcile_failure_aborts_with_hint | core-orchestration-unit |
| commit-intent-journal | CommitIntent model and IStateManager journal API | Set, read, and clear an intent record | tests/state/test_manager.py | test_commit_intent_set_get_clear | state-config-unit |
| commit-intent-journal | CommitIntent model and IStateManager journal API | Upsert replaces the record for the same disk | tests/state/test_manager.py | test_commit_intent_upsert_same_disk | state-config-unit |
| commit-intent-journal | CommitIntent model and IStateManager journal API | Multiple disks hold independent intent records | tests/state/test_manager.py | test_commit_intent_multiple_disks_independent | state-config-unit |
| commit-intent-journal | Atomic persistence with backward-compatible reads | Old state file without commit_in_progress | tests/state/test_recovery_state.py | test_legacy_state_without_commit_in_progress_empty | state-config-unit |
| commit-intent-journal | Atomic persistence with backward-compatible reads | Intent survives a state round-trip | tests/state/test_manager.py | test_commit_intent_survives_round_trip | state-config-unit |
| commit-intent-journal | Intent written before the irreversible commit | Intent precedes the manager call | tests/core/test_pipeline.py | test_intent_precedes_manager_call | core-orchestration-unit |
| commit-intent-journal | Intent cleared only after the outcome is finalized | Success ordering — intent cleared last | tests/core/test_pipeline.py | test_success_outcome_intent_cleared_last | core-orchestration-unit |
| commit-intent-journal | Intent cleared only after the outcome is finalized | Definitive failure clears the intent | tests/core/test_pipeline.py | test_definitive_failure_clears_intent | core-orchestration-unit |
| commit-intent-journal | Intent cleared only after the outcome is finalized | Unknown outcome keeps intent until reconciliation decides | tests/core/test_pipeline.py | test_unknown_keeps_intent_until_finalized | core-orchestration-unit |
| commit-intent-journal | Crash recovery of stale intent records | Stale intent with a completed job self-heals | tests/core/test_pipeline.py | test_stale_intent_completed_job_self_heals | core-orchestration-unit |
| commit-intent-journal | Crash recovery of stale intent records | Stale intent with a live job defers | tests/core/test_pipeline.py | test_stale_intent_live_job_defers | core-orchestration-unit |
| commit-intent-journal | Crash recovery of stale intent records | Stale intent with no effect is discarded | tests/core/test_pipeline.py | test_stale_intent_no_effect_discarded | core-orchestration-unit |
| blockjob-protocol | Shared block-job probe | No job reported | tests/core/test_pipeline.py | test_probe_no_job_returns_none | core-orchestration-unit |
| blockjob-protocol | Shared block-job probe | Active job reported | tests/core/test_pipeline.py | test_probe_active_job_returns_active | core-orchestration-unit |
| blockjob-protocol | Shared block-job probe | Probe call fails | tests/core/test_pipeline.py | test_probe_call_failure_returns_error | core-orchestration-unit |
| blockjob-protocol | Shared block-job probe | Backup path behavior unchanged | tests/core/test_engine.py | test_backup_probe_behavior_unchanged | core-orchestration-unit |
| blockjob-protocol | Probe before blockcommit | Unknown active job blocks a new commit | tests/core/test_pipeline.py | test_unknown_active_job_defers_commit | core-orchestration-unit |
| blockjob-protocol | Probe before blockcommit | Own zombie job is reconciled, not clobbered | tests/core/test_pipeline.py | test_own_zombie_job_reconciled_not_clobbered | core-orchestration-unit |
| blockjob-protocol | Probe before blockcommit | Probe error fails closed | tests/core/test_pipeline.py | test_commit_probe_error_fails_closed | core-orchestration-unit |
| blockjob-protocol | Probe before snapshot creation | Active job defers snapshot creation | tests/core/test_pipeline.py | test_active_job_defers_snapshot_creation | core-orchestration-unit |
| blockjob-protocol | Probe before snapshot creation | All disks clear — snapshot proceeds | tests/core/test_pipeline.py | test_all_disks_clear_snapshot_proceeds | core-orchestration-unit |
| blockjob-protocol | Probe before snapshot creation | Stopped VM skips the probe | tests/core/test_pipeline.py | test_stopped_vm_skips_probe | core-orchestration-unit |
| blockjob-protocol | No automatic block-job abort | Zombie job is deferred, never aborted | tests/core/test_pipeline.py | test_zombie_job_never_aborted | core-orchestration-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Successful live blockcommit of a single snapshot | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_single_snapshot_success | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Live blockcommit fails — virsh returns error | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_virsh_error | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Blockcommit blocked by AppArmor or SELinux | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_blocked_by_apparmor | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Offline commit pivots child and deletes file | tests/modules/lifecycle/test_qemu_img_commit.py | test_qemu_img_commit_pivots_child_and_deletes | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Offline commit of chain tip-of-subset without child | tests/modules/lifecycle/test_qemu_img_commit.py | test_qemu_img_commit_no_child_skips_rebase | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Offline commit failure short-circuits safely | tests/modules/lifecycle/test_qemu_img_commit.py | test_qemu_img_commit_failure_no_delete_short_circuit | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Empty snapshot list — nothing to merge | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_empty_list_no_op | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Blockcommit times out — unknown outcome | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_timeout_returns_unknown | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Injected timeout is honored | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_injected_timeout_honored | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Heartbeat callback invoked during the wait | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_heartbeat_callback_elapsed | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Successful blockcommit with deep verify passing | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_passes | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Successful blockcommit but deep verify fails | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_fails_corruptions | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | deep_verify=False — no check performed | tests/modules/lifecycle/test_blockcommit.py | test_blockcommit_deep_verify_false_no_check | lifecycle-managers-unit |
| lifecycle-manager | Blockcommit snapshots into a disk's base image | Deep verify on the disk's base image (not a VM-level base) | tests/modules/lifecycle/test_qemu_img_commit.py | test_qemu_img_commit_deep_verify | lifecycle-managers-unit |
| result-types | CommitResult dataclass | Successful blockcommit | tests/models/test_results.py | test_commit_result_success_outcome | shell-models-unit |
| result-types | CommitResult dataclass | Unknown outcome from timeout | tests/models/test_results.py | test_commit_result_unknown_outcome | shell-models-unit |
| result-types | CommitResult dataclass | Default outcome preserves legacy constructors | tests/models/test_results.py | test_commit_result_outcome_defaults_failure | shell-models-unit |
| shell-abstraction | run_with_heartbeat execution method | Normal completion before timeout | tests/utils/test_shell.py | test_run_with_heartbeat_normal_completion | shell-models-unit |
| shell-abstraction | run_with_heartbeat execution method | Heartbeat fires while the child runs | tests/utils/test_shell.py | test_run_with_heartbeat_heartbeat_fires | shell-models-unit |
| shell-abstraction | run_with_heartbeat execution method | Hard timeout kills the child | tests/utils/test_shell.py | test_run_with_heartbeat_hard_timeout_kills | shell-models-unit |
| shell-abstraction | run_with_heartbeat execution method | Chatty child does not deadlock the pipes | tests/utils/test_shell.py | test_run_with_heartbeat_chatty_child_no_deadlock | shell-models-unit |
| shell-abstraction | run_with_heartbeat execution method | MockShell implements the contract | tests/mocks/test_mock_shell.py | test_run_with_heartbeat_mock_scripted | mocks-contracts |
| state-management | Commit intent journal persistence | Journal round-trip through JSON state | tests/state/test_manager.py | test_commit_intent_json_round_trip | state-config-unit |
| state-management | Commit intent journal persistence | Journal write is atomic with other state | tests/state/test_manager.py | test_commit_intent_atomic_with_other_state | state-config-unit |
| state-management | Commit intent journal persistence | Legacy state file loads cleanly | tests/state/test_manager.py | test_legacy_state_file_loads_cleanly | state-config-unit |
| config-model | blockcommit_timeout field in GlobalConfig | Default blockcommit timeout is 1800 | tests/config/test_model.py | test_global_config_blockcommit_timeout_default_1800 | state-config-unit |
| config-model | blockcommit_timeout field in GlobalConfig | TOML override is parsed | tests/config/test_parser.py | test_parse_blockcommit_timeout_override | state-config-unit |
| config-model | blockcommit_timeout field in GlobalConfig | Invalid values rejected | tests/config/test_facade.py | test_blockcommit_timeout_invalid_values_rejected | state-config-unit |
| config-model | blockcommit_timeout field in GlobalConfig | Field is immutable | tests/config/test_model.py | test_global_config_blockcommit_timeout_immutable | state-config-unit |
| core-orchestrator | Commit path intent-journal orchestration | Success path ordering | tests/core/test_pipeline.py | test_success_path_state_write_order | core-orchestration-unit |
| core-orchestrator | Commit path intent-journal orchestration | Unknown path keeps intent | tests/core/test_pipeline.py | test_unknown_path_keeps_intent_pre_reconciliation | core-orchestration-unit |
| core-orchestrator | Unknown commit outcome dispatches reconciliation | Timeout no longer aborts before reconciliation | tests/core/test_pipeline.py | test_timeout_unknown_no_abort_late_success | core-orchestration-unit |
| core-orchestrator | Block-job probe before blockcommit | Active unknown job defers the commit | tests/core/test_pipeline.py | test_probe_active_no_intent_defers_commit | core-orchestration-unit |
| core-orchestrator | Block-job probe before snapshot creation | Snapshot creation skipped while a job is active | tests/core/test_pipeline.py | test_snapshot_creation_skipped_job_active | core-orchestration-unit |
| core-orchestrator | Fail-closed offline race guard | Recheck failure defers instead of committing | tests/core/test_lifecycle_fork.py | test_domstate_recheck_failure_defers_vm_state_unknown | core-orchestration-unit |
| core-orchestrator | Configurable commit timeout pass-through | Configured timeout reaches the manager | tests/core/test_pipeline.py | test_configured_timeout_reaches_manager | core-orchestration-unit |
| core-orchestrator | Intent recovery in the deferred-operations step | Stale intent resolved before new commit evaluation | tests/core/test_pipeline.py | test_stale_intent_resolved_before_commit_eval | core-orchestration-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add and retrieve per-disk deferred blockcommit | tests/state/test_manager.py | test_add_and_retrieve_deferred_blockcommit | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Multiple disks can have separate deferred entries | tests/state/test_manager.py | test_multiple_disks_separate_deferred_entries | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with vm_running reason | tests/state/test_manager.py | test_add_deferred_blockcommit_vm_running_reason | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with active_layer reason | tests/state/test_manager.py | test_add_deferred_blockcommit_active_layer_reason | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with enospc reason | tests/state/test_manager.py | test_add_deferred_blockcommit_enospc_reason | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with blockjob_active reason | tests/state/test_manager.py | test_add_deferred_blockcommit_blockjob_active_reason | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Add deferred blockcommit with vm_state_unknown reason | tests/state/test_manager.py | test_add_deferred_blockcommit_vm_state_unknown_reason | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Clear deferred operations | tests/state/test_manager.py | test_clear_deferred_operations | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | No deferred operations for VM | tests/state/test_manager.py | test_no_deferred_operations_empty_list | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | last_warned_at persists across state round-trip | tests/state/test_manager.py | test_state_round_trips_last_warned_at | state-config-unit |
| deferred-operations | Per-disk deferred blockcommit queue in IStateManager | Old state file without last_warned_at is backward-compatible | tests/state/test_manager.py | test_old_state_file_backward_compatible | state-config-unit |
| commit-observability | Commit intent log before the manager call | Intent line precedes every commit attempt | tests/core/test_pipeline.py | test_intent_info_log_precedes_commit | core-orchestration-unit |
| commit-observability | Commit intent log before the manager call | Drain path also logs intent | tests/core/test_deferred.py | test_drain_path_logs_intent_line | core-orchestration-unit |
| commit-observability | Heartbeat during live commit waits | Heartbeat lines appear during a long commit | tests/modules/lifecycle/test_blockcommit.py | test_heartbeat_lines_during_long_commit | lifecycle-managers-unit |
| commit-observability | Heartbeat during live commit waits | Fast commit produces no heartbeat | tests/modules/lifecycle/test_blockcommit.py | test_fast_commit_no_heartbeat_lines | lifecycle-managers-unit |
| commit-observability | Reconciliation and recovery outcomes are logged | Each outcome is distinguishable in the log | tests/core/test_pipeline.py | test_reconciliation_outcomes_logged | core-orchestration-unit |
| blockcommit-recovery | Per-disk chain verification reports broken file | Broken file reported on missing file | tests/core/test_check_snapshots.py | test_check_broken_chain_middle_missing | core-orchestration-unit |
| blockcommit-recovery | Per-disk chain verification reports broken file | No broken file on other failures | tests/core/test_check_snapshots.py | test_check_detects_cycle_in_chain | core-orchestration-unit |
| blockcommit-recovery | Per-disk chain verification reports broken file | Broken file beyond depth 64 is still identified | tests/core/test_check_snapshots.py | test_broken_file_beyond_depth_64_identified | core-orchestration-unit |
| blockcommit-recovery | Per-disk chain verification reports broken file | Walk bound scales with measured chain length | tests/core/test_check_snapshots.py | test_find_broken_chain_walk_bound_scales | core-orchestration-unit |

## Delegation Groups

Groups are split by layer per TESTING.md mirroring. **No test file appears in more than one group.**

### Group: shell-models-unit
**Scope:** `tests/utils/test_shell.py`, `tests/models/test_results.py`
| Test File | Scenarios | Action |
|---|---|---|
| tests/utils/test_shell.py | shell-abstraction: Normal completion before timeout; Heartbeat fires while the child runs; Hard timeout kills the child; Chatty child does not deadlock the pipes | NEW — `run_with_heartbeat` unit tests join the existing `run`/`run_with_stall_detection` tests in this file (mirrors `qsnap/shell/subprocess_shell.py`); monkeypatch poll slices for speed, exactly like the stall-detection tests do with `_POLL_INTERVAL` |
| tests/models/test_results.py | result-types: Successful blockcommit; Unknown outcome from timeout; Default outcome preserves legacy constructors | NEW — extend `test_commit_result_success` area with `outcome` field assertions; also assert `success=True ⇒ outcome="success"` and frozen immutability of `outcome` |

### Group: lifecycle-managers-unit
**Scope:** `tests/modules/lifecycle/` (mirrors `qsnap/modules/lifecycle/blockcommit_manager.py`, `qemu_img_commit.py`)
| Test File | Scenarios | Action |
|---|---|---|
| tests/modules/lifecycle/test_blockcommit.py | lifecycle-manager: Successful live blockcommit of a single snapshot; Live blockcommit fails — virsh returns error; Blockcommit blocked by AppArmor or SELinux; Empty snapshot list — nothing to merge; Blockcommit times out — unknown outcome; Injected timeout is honored; Heartbeat callback invoked during the wait; Successful blockcommit with deep verify passing; Successful blockcommit but deep verify fails; deep_verify=False — no check performed · commit-observability: Heartbeat lines appear during a long commit; Fast commit produces no heartbeat | MODIFY — strip all `virsh domblklist` expectations (manager no longer calls domblklist); rewire call recording through `run_with_heartbeat`; NEW tests for timeout→`outcome="unknown"`, injected timeout, heartbeat callback, heartbeat log lines |
| tests/modules/lifecycle/test_qemu_img_commit.py | lifecycle-manager: Offline commit pivots child and deletes file; Offline commit of chain tip-of-subset without child; Offline commit failure short-circuits safely; Deep verify on the disk's base image (not a VM-level base) | MODIFY — `CountingShell` gains `run_with_heartbeat`; `timeout=` kwarg passed to `qemu-img commit` (default 1800); timeout maps to `outcome="unknown"`; deep-verify asserts `qemu-img check` targets the disk's `base_image` |

### Group: state-config-unit
**Scope:** `tests/state/`, `tests/config/` (mirrors `qsnap/state/json_manager.py`, `qsnap/config/facade.py`, `qsnap/models/config.py`)
| Test File | Scenarios | Action |
|---|---|---|
| tests/state/test_manager.py | commit-intent-journal: Set/read/clear; Upsert; Multiple disks; Intent survives round-trip · state-management: Journal round-trip; Journal write atomic; Legacy state file loads cleanly · deferred-operations: all 11 scenarios | MODIFY — extend the existing `JsonStateManager` suite with the `commit_in_progress` persistence tests (tmp+`os.replace` path already covered by `test_atomic_write_pattern`; add journal-in-same-save assertion); existing deferred tests gain `disk`/`last_warned_at` assertions; NEW tests for `blockjob_active`/`vm_state_unknown` reasons |
| tests/state/test_recovery_state.py | commit-intent-journal: Old state file without commit_in_progress | MODIFY — add legacy-file-without-`commit_in_progress` case to the existing `test_legacy_state_files_load_without_new_fields` area |
| tests/config/test_model.py | config-model: Default blockcommit timeout is 1800; Field is immutable | NEW — `GlobalConfig().blockcommit_timeout == 1800`; `FrozenInstanceError` on assignment (pattern of `test_global_config_immutable`) |
| tests/config/test_parser.py | config-model: TOML override is parsed | NEW — `[global] blockcommit_timeout = 900` → `GlobalConfig.blockcommit_timeout == 900` (pattern of `test_parse_global_section`) |
| tests/config/test_facade.py | config-model: Invalid values rejected | NEW — `0`, `-1`, `"abc"` each rejected with a clear validation error naming the option (pattern of `test_zero_chain_length_rejected`) |

### Group: core-orchestration-unit
**Scope:** `tests/core/` (mirrors `qsnap/core/__init__.py` — `_blockcommit_one_disk`, `_plan_blockcommit`, `_execute_snapshot_steps`, reconciliation helpers, `_find_broken_chain_file`, backup-path probe)
| Test File | Scenarios | Action |
|---|---|---|
| tests/core/test_pipeline.py | commit-reconciliation ×11; commit-intent-journal ×6 (intent precedes manager call, success/definitive-failure/unknown clearing, 3 stale-intent recovery); blockjob-protocol ×10 (probe classification, pre-commit, pre-snapshot, no-abort); core-orchestrator ×7; commit-observability ×2 | MODIFY + NEW — the bulk of the orchestration work lands here: reconciliation helper tests, intent-journal call-order tests (spy `mock_state` methods), probe classification/parse tests, step-0 recovery tests, log-line tests. Existing blockcommit tests must script a default `virsh blockjob` → "No current block job" expectation (added centrally in `tests/conftest.py`) and pass `timeout=` to the mock manager |
| tests/core/test_engine.py | blockjob-protocol: Backup path behavior unchanged | MODIFY — after refactoring the backup-path probe onto the shared helper, the existing "blockjob active defers backup" tests keep passing unchanged; add a spy assertion that the backup probe now routes through `_probe_blockjob` while producing the same deferral (INFO log, no baseline update, not a failure) |
| tests/core/test_deferred.py | commit-observability: Drain path also logs intent | MODIFY — drain tests (`test_drain_shutoff_uses_qemu_img_executor`, `test_drain_running_virsh_mode_commits_non_active`, ENOSPC drain) gain: intent write before drain commit, `[blockcommit] ... (mode=..., timeout=...)` INFO line, intent cleared on success, probe-before-drain-commit |
| tests/core/test_lifecycle_fork.py | core-orchestrator: Recheck failure defers instead of committing | MODIFY — extend the race-guard test: existing `test_blockcommit_race_guard_defers_when_vm_started` (recheck → "running" → defer `vm_running`) stays; NEW sibling where the recheck `ShellResult(success=False)` → defer `vm_state_unknown` and NO `qemu-img commit` command is issued |
| tests/core/test_check_snapshots.py | blockcommit-recovery ×4 | MODIFY — `test_check_broken_chain_middle_missing` asserts `ChainVerifyResult.broken_file == Path(snap2)`; `test_check_detects_cycle_in_chain` asserts `broken_file is None` and pipeline abort; NEW deep-chain tests with a scripted 73-layer `qemu-img info --backing-chain` payload (broken file at layer 70) and a 90-layer payload asserting walk bound ≥ 92 |
| tests/conftest.py | (support fixture — no scenario rows) | MODIFY — add default `virsh blockjob` → `stdout="No current block job\n"` expectation to `_setup_validation_expectations`; extend `make_global_config` with `blockcommit_timeout: int = 1800` |

### Group: mocks-contracts
**Scope:** `tests/interfaces/`, `tests/mocks/` (contract tests + all ABC mock implementations)
| Test File | Scenarios | Action |
|---|---|---|
| tests/interfaces/test_shell.py | (contract for shell-abstraction) | MODIFY — assert `run_with_heartbeat` in `IShell.__abstractmethods__` with exact signature (`cmd`, `timeout`, `heartbeat_seconds`, `on_heartbeat`, `check=False`) and `ShellResult` return hint (pattern of `test_ishell_has_run_with_stall_detection`) |
| tests/interfaces/test_state_manager.py | (contract for commit-intent-journal) | MODIFY — assert `set_commit_in_progress` / `get_commit_in_progress` / `clear_commit_in_progress` in `IStateManager.__abstractmethods__`; parametrize a behavior round-trip over `[JsonStateManager, InMemoryStateManager]` |
| tests/interfaces/test_lifecycle_manager.py | (contract for lifecycle-manager timeout kwarg) | MODIFY — extend `test_lifecycle_manager_blockcommit_returns_commit_result` parametrization to call `blockcommit(..., timeout=900)` on all three implementations and assert the result is a `CommitResult` |
| tests/mocks/mock_shell.py | (implementation of run_with_heartbeat) | MODIFY — implement scripted `run_with_heartbeat`: record the call, invoke `on_heartbeat(elapsed)` the scripted number of times, return the scripted `ShellResult`; add expectation API support for heartbeat count |
| tests/mocks/mock_state.py | (implementation of intent journal) | MODIFY — add `set_commit_in_progress` / `get_commit_in_progress` / `clear_commit_in_progress` backed by a `dict[tuple[vm,disk], CommitIntent]` with upsert semantics; also clear per-disk intent in `reset_vm_disk_state` |
| tests/mocks/mock_modules.py | (MockLifecycleManager contract) | MODIFY — `MockLifecycleManager.blockcommit` accepts `timeout: int = 1800` kwarg and returns `CommitResult(..., outcome="success")` on success / `outcome="failure"` on failure |
| tests/mocks/mock_factory.py | (pass-through) | MODIFY — only if the factory interface changes; otherwise verify `create_lifecycle_manager(mode)` unchanged |
| tests/mocks/test_mock_shell.py | shell-abstraction: MockShell implements the contract | MODIFY — scripted heartbeat test: `expect(...).returns(result, heartbeats=2)` → records call, invokes callback twice, returns result; `isinstance(mock_shell, IShell)` still True |
| tests/mocks/test_mock_state.py | (mock parity for intent journal) | MODIFY — `InMemoryStateManager` intent set/get/clear/upsert/multi-disk parity tests mirroring the `JsonStateManager` ones in `tests/state/test_manager.py` |
| tests/mocks/test_mock_validity.py | (mock ABC validity) | MODIFY — `test_mock_shell_implements_full_interface` also asserts `run_with_heartbeat` exists/callable; add `isinstance(InMemoryStateManager(), IStateManager)` intent-method presence check |

### Group: integration-stress
**Scope:** `tests/integration/`, `tests/stress/` (real libvirt; `@pytest.mark.integration` / `@pytest.mark.stress`)
| Test File | Scenarios | Action |
|---|---|---|
| tests/integration/test_blockcommit_defer.py | (live/offline commit + probe + intent against real libvirt) | MODIFY — each existing test that reaches the commit path gains: pre-commit `virsh blockjob` probe assertions (real probe returns "No current block job" on an idle disk), intent-record presence/clearing assertions, and `[blockcommit]` intent log assertions |
| tests/integration/test_commit_intent_recovery.py | (NEW — full program in the Integration & Synthetic section) | NEW — real-libvirt validation of outcome classification, probe parsing, intent journal durability, stale-intent recovery, and foreign-job deferral |
| tests/stress/test_long_chain.py | blockcommit-recovery depth fix on a real chain | MODIFY — after the existing 55-layer prune test, assert the broken-file walk still identifies a missing file when the chain exceeds 64 layers (create a deliberately truncated overlay, run `check`); the `_CHAIN_DEPTH = 55` fixture proves the dynamic `max(64, measured+2)` bound engages |

## Test Modifications

| File | Change | Reason |
|---|---|---|
| tests/conftest.py | Add default `virsh blockjob` → `stdout="No current block job\n"` expectation; add `blockcommit_timeout=1800` to `make_global_config` | Every Core blockcommit test now triggers a pre-commit probe (D6); without the default expectation the probe classifies "No mock configured" as `"error"` and every existing commit test would defer instead of commit. GlobalConfig gains the field (config-model spec) |
| tests/modules/lifecycle/test_blockcommit.py | Remove the `_DOMBLKLIST_OUTPUT` constant and every `mock_shell.expect("virsh domblklist")` block (14 call sites); `CountingShell` implements `run_with_heartbeat`; docstring updated | The manager no longer calls `virsh domblklist` — `disk` is a keyword-only parameter (lifecycle-manager spec, "MUST NOT derive via domblklist") |
| tests/modules/lifecycle/test_blockcommit.py | `test_blockcommit_deep_verify_*` tests: no domblklist expectation; assert `qemu-img check` targets the disk's `base_image` and that the internal `shell.run()` call does NOT pass `check=True` | lifecycle-manager deep-verify requirement ("The helper's internal shell.run() call SHALL NOT pass check=True") |
| tests/modules/lifecycle/test_qemu_img_commit.py | `CountingShell` implements `run_with_heartbeat`; commit-timeout assertions changed from implicit 3600 to explicit injected `timeout=` | D3: `qemu-img commit` uses injected timeout (default 1800) |
| tests/interfaces/test_shell.py | Add `run_with_heartbeat` abstract-method + signature contract test | D2 BREAKING `IShell` addition; contract tests must parametrize over `SubprocessShell` (and mock) |
| tests/interfaces/test_state_manager.py | Add intent-journal abstract-method + behavior contract parametrized over `JsonStateManager` / `InMemoryStateManager` | D4 BREAKING `IStateManager` addition |
| tests/interfaces/test_lifecycle_manager.py | Contract calls pass `timeout=` and assert `CommitResult.outcome` present | D3 additive `timeout` parameter; result-types spec |
| tests/mocks/mock_shell.py | Implement `run_with_heartbeat` (records call, scripted heartbeats) | All `IShell` implementations must implement it (shell-abstraction spec) |
| tests/mocks/mock_state.py | Implement intent journal methods; `reset_vm_disk_state` also clears per-disk intent | All `IStateManager` implementations must implement it (commit-intent-journal spec) |
| tests/mocks/mock_modules.py | `MockLifecycleManager.blockcommit(..., timeout=1800)` + `outcome` in returned `CommitResult` | D3 + result-types spec; keeps contract tests green |
| tests/core/test_pipeline.py | Existing blockcommit tests script `virsh blockjob` "none" and `timeout=` kwargs; state-write spies extended to `set/clear_commit_in_progress`; dry-run test's read-only pattern list already tolerates `virsh blockjob` (no change needed there) | New pre-commit probe (D6), intent journal (D4), timeout pass-through (D3) |
| tests/core/test_lifecycle_fork.py | Race-guard test scripts the blockjob probe between domstate calls; new recheck-failure case | D6 probe ordering; D7 fail-closed guard |
| tests/core/test_deferred.py | Drain tests assert intent write/clear around drain commits and the drain `[blockcommit]` INFO line | D4 (intent in deferred drain), D9 observability |
| tests/core/test_check_snapshots.py | Broken-chain tests assert `ChainVerifyResult.broken_file` / `disk` fields; new deep-chain fixtures | blockcommit-recovery spec (broken_file regardless of depth, `None` for non-missing causes) |
| tests/state/test_manager.py | Existing deferred tests assert `disk` field; new `commit_in_progress` persistence tests | deferred-operations spec (per-disk entries); D4 journal persistence |
| tests/core/test_recovery_pipeline.py | `test_last_commit_ts_written_after_blockcommit` and offline twin: assert intent cleared AFTER `last_commit_ts` write (order spy); script blockjob probe | commit-intent-journal "Success ordering — intent cleared last" |
| tests/integration/test_blockcommit_defer.py | Add real probe assertions ("No current block job"), intent record checks, and `[blockcommit]` log checks to the 4 commit-path tests | Real-libvirt proof of D4/D6/D9 behavior (see Integration program) |
| tests/stress/test_long_chain.py | After the 55-layer prune, run `check` with a truncated overlay to exercise the dynamic walk bound | blockcommit-recovery depth-64 fix on a real chain |

## Tests To Delete

| File | Test | Reason |
|---|---|---|
| tests/modules/lifecycle/test_blockcommit.py | `test_blockcommit_timeout` | Asserts the OLD timeout-is-definitive-failure contract only (returns `CommitResult(success=False, error contains "timed out")` with no outcome semantics, and scripts the stale `domblklist` call). Replaced by NEW `test_blockcommit_timeout_returns_unknown` per lifecycle-manager scenario "Blockcommit times out — unknown outcome" (`outcome="unknown"`, error `"Command timed out after {timeout}s"`, never classified failure by the manager) |
| tests/modules/lifecycle/test_blockcommit.py | The `_DOMBLKLIST_OUTPUT` module constant and all 14 `mock_shell.expect("virsh domblklist").returns(...)` blocks embedded in surviving tests | Stale expectations for a `virsh domblklist` call that no longer exists inside `BlockCommitManager` (disk is a keyword-only parameter). The host tests are modified, not deleted; the expectation blocks are deleted |
| tests/modules/lifecycle/test_blockcommit.py | `CountingShell` (old two-method form) | Stale test double: it only wraps `run`/`run_with_stall_detection` and cannot record `run_with_heartbeat` calls the new manager makes. Reimplemented (with `run_with_heartbeat`) as part of the file modification |
| tests/core/test_pipeline.py | No whole-test deletions; `test_blockcommit_live_commit_when_vm_running` and similar commit-path tests must be MODIFIED (script probe + timeout kwarg), not deleted | Retained behavior remains valid; only the fixture wiring changes |
| Any test asserting a hard-coded 3600 s commit timeout elsewhere in the commit path | None found — the only 3600-commit-path assertion in the suite is the `"virsh blockcommit timed out after 3600 seconds"` string inside `test_blockcommit_timeout` (deleted above). `tests/utils/test_shell.py` stall tests and `tests/core/test_pipeline.py` `_deep_check_file` 7200 s tests are outside the blockcommit path and untouched | Verification performed via `rg "3600" tests/` — no other commit-path occurrence |

## Integration & Synthetic Test Program

### (a) SYNTHETIC tests — MockShell-scripted exact libvirt/virsh output strings

These unit/mock tests intercept the EXACT output formats libvirt/virsh produce in the field, so error interception is verified against realistic strings rather than invented formats. The `virsh blockjob` strings below are taken from the existing backup-path probe expectations (tests/modules/backup/test_bitmap.py uses `stdout="No current block job\n"`) and libvirt's documented job-report format.

| Simulated output (exact string) | Test File / Test Name | Interception verified |
|---|---|---|
| `virsh blockjob` stdout = `"No current block job\n"`, exit 0 | tests/core/test_pipeline.py::test_probe_no_job_returns_none | Helper classifies `"none"`; commit proceeds |
| `virsh blockjob` stdout = `"Block job: type=blockcommit\nBandwidth limit: 0 B/s\nJob: 1048576/2097152\n"`, exit 0 | tests/core/test_pipeline.py::test_probe_active_job_returns_active | Any job-describing output classifies `"active"` (not just the literal "Active block job exists" phrase) |
| `virsh blockjob` stderr = `"error: failed to get job info for disk vda"`, exit 1 | tests/core/test_pipeline.py::test_probe_call_failure_returns_error | Failed probe → `"error"` → fail-closed `vm_state_unknown` deferral |
| `virsh blockjob` stdout = `"Active block job exists"` (job-reject wording used by libvirt when starting a competing job) | tests/core/test_pipeline.py::test_unknown_active_job_defers_commit | Active + no intent → defer `blockjob_active`, no `virsh blockcommit` issued, WARNING names VM+disk |
| `ShellResult(error="Command timed out after 1800s", returncode=-1)` returned by `run_with_heartbeat` for `virsh blockcommit --wait` | tests/modules/lifecycle/test_blockcommit.py::test_blockcommit_timeout_returns_unknown | Manager maps timeout/kill → `CommitResult(success=False, outcome="unknown")`, never failure |
| Same timeout result plus reconciliation probe outputs (no job + files gone + chain shorter) | tests/core/test_pipeline.py::test_reconcile_late_success_after_timeout | Core runs reconciliation on `unknown`; classifies `late_success`; no `RuntimeError` |
| `virsh blockcommit` stderr = `"error: Failed to pivot snapshot: Permission denied\nlibvirt: AppArmor denial: cannot access /var/lib/libvirt/images"` | tests/modules/lifecycle/test_blockcommit.py::test_blockcommit_blocked_by_apparmor | `detect_mac_denial` → `error="blocked by apparmor"`, `committed_snapshot=""`, `outcome="failure"` |
| `virsh blockcommit` stderr = `"error: internal error: Operation not permitted\nSELinux: AVC denied: { read } for qemu"` | tests/modules/lifecycle/test_blockcommit.py::test_blockcommit_blocked_by_selinux | `detect_mac_denial` → `error="blocked by selinux"`, `outcome="failure"` |
| `qemu-img commit` stderr = `"qemu-img: error while writing to output file: No space left on device"` | tests/core/test_deferred.py::test_offline_commit_enospc_defers_no_runtime_error (MODIFIED) | Space-classified failure → `enospc` deferral (no VM abort), intent cleared per definitive-failure rule |
| `virsh domstate` recheck returns `ShellResult(success=False, error="cannot connect to libvirt")` | tests/core/test_lifecycle_fork.py::test_domstate_recheck_failure_defers_vm_state_unknown | Offline race guard fails CLOSED: defer `vm_state_unknown`, zero `qemu-img commit` commands |
| Stale-intent recovery script: `virsh blockjob` "No current block job" + `os.path.exists(snap)=False` + `qemu-img info --backing-chain` one layer shorter | tests/core/test_pipeline.py::test_stale_intent_completed_job_self_heals | Step-0 crash recovery converges state (WARNING "completed after previous run timed out"), clears intent, continues |
| Stale-intent with no effect: "No current block job" + all files exist + chain unchanged | tests/core/test_pipeline.py::test_stale_intent_no_effect_discarded | Intent cleared with WARNING, disk proceeds to normal retention evaluation |

### (b) INTEGRATION tests (real libvirt, `@pytest.mark.integration`, disposable `test_vm` fixture)

The existing integration suite has no `test_stale_state_recovery.py`; the closest analogues are `tests/integration/test_blockcommit_defer.py` (commit-path) and `tests/integration/test_reconcile_snapshots.py`. The program below both updates the former and adds one new file.

**UPDATED — tests/integration/test_blockcommit_defer.py**

| Test | Update | What it proves that mocks cannot |
|---|---|---|
| `test_live_commit_non_active_while_running_integration` | Before `_blockcommit_snapshots`, run the real probe `virsh blockjob --domain <vm> --path vda` and assert stdout parses as `"none"`; after commit, assert `get_commit_in_progress(vm)` is empty and `last_commit_ts` is set; assert a `[blockcommit]` INFO line preceded the commit | The probe parser works against REAL libvirt's exact idle-disk output; the intent journal round-trips through the real commit path against real libvirt/QEMU |
| `test_active_layer_deferred_running_integration` | Same probe assertion; assert no intent record is written for the deferred active layer | Deferral paths never write intent (intent precedes only irreversible commits) |
| `test_deferred_blockcommit_executes_after_shutdown_integration` | After drain, assert the drain wrote and cleared an intent record and emitted the `[blockcommit]` intent line | The deferred-drain path (D4/D9) works against real offline `qemu-img commit` |
| `test_offline_commit_enospc_defers_then_drains_integration` | After the ENOSPC deferral, assert intent was cleared (definitive failure) and no intent lingers; after the real drain, assert intent write→clear ordering | Definitive-failure intent clearing against a real filesystem/state interaction |

**NEW — tests/integration/test_commit_intent_recovery.py**

| Test | Scenario it validates | What it proves that mocks cannot |
|---|---|---|
| `test_real_probe_idle_disk_returns_none` | Real `virsh blockjob --domain <vm> --path vda` on an idle disk returns "No current block job" → `_probe_blockjob` → `"none"` | The parse of REAL libvirt output (wording, trailing newline, locale) — mock strings are only approximations |
| `test_real_blockcommit_produces_success_outcome` | Create 2 snapshots, commit the oldest via Core with `DefaultFactory` + `SubprocessShell`; assert `outcome="success"`, merge-set file deleted, chain shortened, intent record present during the call and cleared after, `last_commit_ts` written | Real QEMU block job completion maps to `outcome="success"`; the manager's `run_with_heartbeat` returns exit-0 results correctly for a real `virsh blockcommit --wait` |
| `test_intent_journal_survives_real_run` (JsonStateManager) | Drive the commit with `JsonStateManager` in a temp `state_dir`; mid-run assert `get_commit_in_progress` returns the record; re-instantiate the manager from the same file and assert the record is identical; after the run assert it is cleared | Atomic tmp+`os.replace` durability of the intent journal across real process/state boundaries; a crash at any point would leave a readable record |
| `test_stale_intent_real_recovery_converges_state` | Manually plant a stale intent record for a disk whose merge-set file was already deleted by a prior real commit; run pipeline step 0; assert WARNING, `last_commit_ts`/`remove_snapshot` convergence, intent cleared, VM not failed | The crash-recovery path's file-existence + chain-length agreement checks against REAL filesystem and REAL `qemu-img info --backing-chain` output (mocks cannot prove the two evidence sources agree in practice) |
| `test_active_foreign_blockjob_defers` | Start a real background `virsh blockcommit --wait` (outside qsnap), run qsnap's pre-commit probe; assert `"active"`, deferred entry with reason `blockjob_active`, no second commit started, VM pipeline continues, no `blockjob --abort` command issued | The "Active block job exists" real output format; the never-clobber rule and no-auto-abort invariant against a genuinely active libvirt job |

**Stress — tests/stress/test_long_chain.py** (MODIFY): after the existing 55-layer prune, create a broken chain by removing a mid-chain overlay file and run `core.check()`; assert `broken_file` identifies the missing file (proves the dynamic `max(64, measured+2)` bound engages on a real chain that exceeds the old hard cap of 64).

## Risks & Edge Cases

Each risk from `design.md` §Risks is mapped to a dedicated test.

| Risk (design.md) | Dedicated test(s) | Notes |
|---|---|---|
| Extra virsh probe calls per run (1 per disk pre-commit, pre-snapshot, recovery) | tests/core/test_pipeline.py::test_probe_issued_before_commit_and_snapshot (NEW) + `test_reconcile_probe_failure_inconclusive` | Asserts exactly one probe per disk before commit and one per disk before snapshot creation for running VMs; asserts the probe command is `virsh blockjob --domain <vm> --path <disk>` with a 30 s timeout recorded by MockShell |
| Late-success misclassification if an external actor deleted files | tests/core/test_pipeline.py::test_reconcile_contradictory_evidence_inconclusive + `test_reconcile_partial_deletion_inconclusive` (NEW — s1 gone, s2 present, chain unchanged) | Classification requires file check AND chain-length check to agree; any disagreement → `inconclusive` → fail-closed `vm_state_unknown` deferral |
| Intent journal left behind if qsnap never runs again | tests/state/test_recovery_state.py::test_legacy_state_without_commit_in_progress_empty + tests/state/test_manager.py::test_legacy_state_file_loads_cleanly + `test_stale_intent_no_effect_discarded` | Inert data is read-tolerantly; the next run surfaces it via WARNING and resolves it |
| Reader threads in `run_with_heartbeat` | tests/utils/test_shell.py::test_run_with_heartbeat_chatty_child_no_deadlock (+ thread-join assertion: after process exit the captured output is complete and no thread leaks) | Child writes >64 KB to both pipes while running; full output returned; daemon threads joined with bounded wait (D2) |
| BREAKING ABC changes (`IShell`, `IStateManager`) | tests/interfaces/test_shell.py (run_with_heartbeat abstract), tests/interfaces/test_state_manager.py (intent journal abstract + behavior over both implementations), tests/interfaces/test_lifecycle_manager.py (timeout kwarg over all 3 implementations) | Contract tests parametrize over ALL implementations; mocks updated in the same change |
| `blockcommit_timeout` default lowered 3600 → 1800 | tests/config/test_model.py::test_global_config_blockcommit_timeout_default_1800; tests/core/test_pipeline.py::test_slow_commit_unknown_reconciled_late_success (NEW — commit exceeds 1800 s → `unknown` → reconciliation → `late_success`); integration `test_real_blockcommit_produces_success_outcome` | A slow-but-completed job becomes `unknown` and is reconciled to `late_success`, so the lower default is safe (D3 risk note) |
| Pre-snapshot probe defers snapshots while a zombie job lingers | tests/core/test_pipeline.py::test_active_job_defers_snapshot_creation + `test_snapshot_creation_skipped_job_active` | Snapshot creation skipped with WARNING; change-detection baseline NOT updated (onchange gate stays open); NO deferred-queue entry created; next run retries |
| (proposal) No automatic `virsh blockjob --abort` | tests/core/test_pipeline.py::test_zombie_job_never_aborted | Asserts zero shell commands containing `blockjob --abort` across all probe/defer paths; WARNING text contains VM name and disk `vda` |
| (blockcommit-recovery) Fixed 64-iteration cap truncates the walk | tests/core/test_check_snapshots.py::test_broken_file_beyond_depth_64_identified + `test_find_broken_chain_walk_bound_scales`; stress test_long_chain.py | 73-layer chain, missing file at layer 70 → `broken_file` set; 90-layer parsed chain → walk allowed ≥92 iterations; real-chain stress proves it end-to-end |
