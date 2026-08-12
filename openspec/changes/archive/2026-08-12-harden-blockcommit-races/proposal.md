# Proposal: harden-blockcommit-races

## Why

On 2026-08-12 a production run hung for a full hour inside `virsh blockcommit --wait`
(`BlockCommitManager`, `shell.run(timeout=3600)`). Forensics proved the QEMU block job
**physically completed** (the overlay was merged and deleted — verified via `virsh dumpxml`),
while only the virsh client's `--wait` hung until qsnap's 3600 s timeout SIGKILLed it. qsnap
then classified the timeout as a **definitive failure**, reported the VM as FAILED, left state
diverged from reality until the next run's stale-entry self-heal, and had no record that a
commit had even been attempted. Follow-up runs also showed commit times degrading from ~5 s to
11 min for a 448 KiB merge, exposing the same hang window repeatedly.

The root cause is a class of **distributed race conditions**: qsnap assumes "command returned =
operation finished", while libvirt/QEMU live by "client died = operation continues". Concretely:
timeout is conflated with failure (no reconciliation with reality); a crash between a successful
commit and the state write leaves phantom entries with no intent record; the offline-commit race
guard fails OPEN when the `domstate` re-check itself fails; the commit path never probes for an
active block job (the backup path already does); and there is zero log output between
`[snapshot] created` and `[blockcommit] merged`, so a hung commit is invisible for an hour.

## What Changes

- **Three-valued commit outcomes.** `CommitResult` gains an `outcome` field:
  `success | failure | unknown`. Timeouts and killed processes are `unknown`, never `failure`.
  Core MUST NOT treat `unknown` as a definitive failure.
- **Post-timeout reconciliation.** After an `unknown` outcome Core reconciles against reality
  (`virsh blockjob --info` + backing-chain length + top-file existence) and classifies the
  result as LATE SUCCESS (state is written, WARNING logged), JOB STILL ACTIVE (defer), or
  TRUE FAILURE (abort the VM).
- **Intent journal (WAL-lite).** Before each irreversible commit Core writes an atomic
  `commit_in_progress` record (`disk`, `snapshots`, `base`, `started_ts`) to state; it is
  cleared only after the outcome is finalized. The crash window becomes observable and zombie
  jobs become attributable to qsnap.
- **Block-job protocol.** Before any chain-mutating operation (blockcommit AND snapshot
  creation) Core probes `virsh blockjob`. No job → proceed; active job that matches our intent
  record → wait/reconcile; active unknown job → defer with WARNING, never start a competing
  operation. No automatic `--abort` — abort is an operator action.
- **Fail-closed race guard.** When the pre-commit `domstate` re-check fails (VM state
  unknown), Core SHALL defer the commit instead of proceeding with `qemu-img commit`.
- **Commit observability.** INFO log before every commit attempt and a heartbeat log every
  60 s while `virsh blockcommit --wait` runs, via a new `IShell.run_with_heartbeat` execution
  method (hard timeout + periodic callback).
- **Configurable commit timeout.** New global option `blockcommit_timeout` (default 1800 s,
  was hard-coded 3600 s), passed to the lifecycle manager as a method parameter.
- **Chain-walk depth fix.** `_find_broken_chain_file` is bounded dynamically by the measured
  chain length instead of a hard cap of 64 iterations (a 73-deep chain exceeded it and the
  broken file was silently reported as `None`).
- **New deferred-operation reasons:** `blockjob_active`, `vm_state_unknown`.

**BREAKING:** `IShell` gains the abstract method `run_with_heartbeat`; `IStateManager` gains
abstract methods for the commit intent journal. All implementations and mocks must be updated.

**Migration:** state files need no migration — `commit_in_progress` is read with default
`None` when absent.

## Capabilities

### New Capabilities

- `commit-reconciliation`: three-valued commit outcomes and the post-UNKNOWN reconciliation
  protocol that treats observed reality (block-job state, chain length, file existence) as the
  source of truth, including late-success detection.
- `commit-intent-journal`: atomic `commit_in_progress` intent record written before every
  irreversible commit and cleared after finalization; crash-window observability and zombie-job
  attribution.
- `blockjob-protocol`: unified block-job probing before chain-mutating operations (commit and
  snapshot creation), job classification (none / ours / unknown), defer-never-clobber rules,
  no automatic abort.
- `commit-observability`: intent logging before risky calls and periodic heartbeat logging
  during long commit waits.

### Modified Capabilities

- `lifecycle-manager`: `BlockCommitManager` uses the injected timeout (no hard-coded 3600 s),
  runs commits through `run_with_heartbeat`, and maps timeout/kill to `outcome="unknown"`.
- `result-types`: `CommitResult` gains `outcome: str` (`success | failure | unknown`).
- `state-management`: `IStateManager` gains commit intent journal methods; `JsonStateManager`
  persists `commit_in_progress` per VM with the existing atomic tmp+`os.replace` write.
- `core-orchestrator`: commit path writes/clears the intent journal, reconciles `unknown`
  outcomes, probes for block jobs before commit and before snapshot creation, and the offline
  race guard fails closed; `_find_broken_chain_file` bound becomes dynamic.
- `blockcommit-recovery`: broken-file identification SHALL work for chains deeper than 64
  layers.
- `deferred-operations`: new deferral reasons `blockjob_active` and `vm_state_unknown`.
- `config-model`: new global option `blockcommit_timeout` (seconds, default 1800).
- `shell-abstraction`: new `IShell.run_with_heartbeat(cmd, timeout, heartbeat_seconds,
  on_heartbeat)` execution method — hard timeout plus periodic callback; `SubprocessShell`
  implements it with a poll loop that always enforces the maximum timeout.

## Impact

- **interfaces/**: `IShell` (+`run_with_heartbeat`), `IStateManager` (+ intent journal methods),
  `ILifecycleManager` (additive keyword `timeout` parameter) — **BREAKING** for implementations
  and mocks.
- **models/results.py**: `CommitResult.outcome` field (additive, defaulted).
- **shell/subprocess_shell.py**: new `run_with_heartbeat` implementation.
- **state/json_manager.py**: `commit_in_progress` persistence; **tests/mocks/mock_state.py**
  (`InMemoryStateManager`) must implement the new methods.
- **modules/lifecycle/blockcommit_manager.py**: timeout injection, heartbeat, unknown mapping.
- **core/__init__.py**: `_blockcommit_one_disk`, `_plan_blockcommit`, `_execute_snapshot_steps`
  (pre-create probe), reconciliation helpers, `_find_broken_chain_file`.
- **config/**: `GlobalConfig.blockcommit_timeout`, TOML parsing, `qsnap.toml.example`.
- **factory/**: pass-through only (timeout travels as a method parameter, per the DI paradigm).
- **tests/**: mock_shell, mock_state, mock_modules, contract parametrizations, lifecycle unit
  tests (stale `domblklist` expectation removed), new integration scenarios.
- **Out of scope (documented follow-ups):** per-VM pipeline deadline (D4), systemd
  `TimeoutStartSec` policy (D7), `run_with_stall_detection` pipe-drain/max-timeout hardening in
  the backup world (D9), orphan checkpoint cleanup on the production host (D10, operational).
