# Design: harden-blockcommit-races

## Context

qsnap orchestrates external qcow2 snapshots and blockcommits through `virsh`/`qemu-img`,
treating every external command as synchronous: "command returned = operation finished".
libvirt/QEMU follow a different contract: "client died = operation continues". The 2026-08-12
incident proved the mismatch: `virsh blockcommit --wait` hung 3600 s and was SIGKILLed, while
the QEMU block job completed on its own (verified via `virsh dumpxml`: the overlay was merged
and deleted). qsnap reported FAILED, wrote no state, and left no record that a commit had been
attempted; the divergence self-healed only on the next run via the stale-entry filter.

All qsnap races are **distributed** races against an asynchronous external world (there are no
threads in qsnap itself). The affected seams:

- `BlockCommitManager.blockcommit` → `IShell.run(timeout=3600)` — timeout conflated with failure.
- `Core._blockcommit_one_disk` (core/__init__.py:4651-4920) — state written only after success;
  crash window between virsh success and `set_last_commit_ts`/`remove_snapshot`.
- Offline race guard (core/__init__.py:4746-4766) — fails OPEN when the `domstate` re-check
  itself fails.
- Commit path has no `virsh blockjob` probe (the backup path already has one,
  core/__init__.py:5264-5295).
- Zero log lines between `[snapshot] created` and `[blockcommit] merged`.
- `_find_broken_chain_file` hard-bounded at 64 iterations (core/__init__.py:4276); production
  chains run 73+ layers.

Constraints: zero runtime PyPI dependencies (stdlib only); DI paradigm (modules are stateless
workers implementing ABCs; Core mediates all coordination); config arrives as frozen dataclass
method parameters; expected failures are result objects, never exceptions; all external commands
go through `IShell`.

## Goals / Non-Goals

**Goals:**

- An `unknown` commit outcome (timeout, kill, crash) is never treated as a definitive failure.
- After any `unknown` outcome, qsnap reconciles against observed reality and converges state
  with it (late-success detection).
- A crashed/killed mid-commit run leaves an observable, attributable trail (intent journal) and
  is resolved on the next run without operator action.
- qsnap never starts a chain-mutating operation (commit or snapshot creation) while a block job
  is active on the same disk, and never aborts a job automatically.
- The offline race guard fails closed.
- A hung commit is visible in logs within 60 s (intent log + heartbeat).
- Broken-file identification works beyond chain depth 64.
- Commit timeout is configurable (`blockcommit_timeout`, default 1800 s).

**Non-Goals:**

- Per-VM pipeline deadline / hang isolation in `_run_pipeline` (defect D4 — separate change).
- systemd `TimeoutStartSec` policy (D7 — operator decision, documented only).
- Hardening `run_with_stall_detection` in the backup world (D9 — separate change).
- Orphan checkpoint cleanup on production hosts (D10 — operational).
- Extending the intent journal to the backup world (`qemu-img convert`) — commit path only.
- Automatic `virsh blockjob --abort` under any circumstance.
- Hysteresis retention / batch-collapse of multiple snapshots in one block job.

## Decisions

### D1. Three-valued commit outcome via additive `CommitResult.outcome`

`CommitResult` gains `outcome: str` with values `"success" | "failure" | "unknown"`,
defaulting to `"failure"` so every existing constructor call keeps working unchanged. Producers
set it explicitly from this change on. `success=True` implies `outcome="success"`.

- Timeout or SIGKILL of the commit command → `CommitResult(success=False, outcome="unknown",
  error="Command timed out after Ns")`.
- Non-zero exit with real stderr → `outcome="failure"`.
- **Alternative considered:** replacing `success: bool` with an enum only. Rejected: breaks
  every caller and mock for no behavioral gain; the additive field keeps the diff small and
  contract tests parametrize cleanly.

### D2. New `IShell.run_with_heartbeat` execution method (BREAKING)

```
IShell.run_with_heartbeat(
    cmd: list[str],
    timeout: int,
    heartbeat_seconds: int,
    on_heartbeat: Callable[[int], None],   # receives elapsed seconds
    check: bool = False,
) -> ShellResult
```

Semantics:

- Runs `cmd` via `Popen` with stdout/stderr pipes.
- Polls `proc.wait(timeout=heartbeat_seconds)`; on each poll expiry calls
  `on_heartbeat(elapsed)`.
- **Hard maximum:** when elapsed >= timeout, kills the process and returns
  `ShellResult(success=False, returncode=-1, error="Command timed out after {timeout}s")`.
  Unlike `run_with_stall_detection`, this method ALWAYS enforces the wall-clock ceiling.
- **Pipe draining:** stdout/stderr are drained continuously by two daemon reader threads into
  in-memory buffers, so a chatty child (e.g. `virsh blockcommit --verbose` progress lines) can
  never block on a full pipe buffer. Threads are joined with a bounded wait after process exit.
- On normal exit, returns `ShellResult` with captured stdout/stderr and returncode.

`SubprocessShell` implements it; `MockShell` implements it script-based (records the call,
invokes `on_heartbeat` a configured number of times, returns the scripted result). All other
`IShell` implementations must implement it — hence BREAKING.

- **Alternative considered:** heartbeat via a Core-side background thread around
  `IShell.run`. Rejected: puts subprocess lifecycle knowledge in Core and cannot produce a
  heartbeat while `subprocess.run` is blocked; the shell is the right owner of execution
  mechanics.

### D3. Timeout injection, no hard-coded 3600 s

- New global option `[global] blockcommit_timeout` (integer seconds, default **1800**, must be
  > 0) on `GlobalConfig`; parsed by `ConfigFacade`; documented in `qsnap.toml.example`.
- `ILifecycleManager.blockcommit` gains additive keyword parameter `timeout: int = 1800`.
  Core always passes it explicitly from `GlobalConfig.blockcommit_timeout` (config travels as
  method parameters per the DI paradigm; nothing stored in modules).
- `BlockCommitManager` uses it for the `virsh blockcommit` call; `QemuImgCommitManager` uses it
  for the `qemu-img commit` call (both currently hard-code 3600).
- Heartbeat interval is a constant **60 s** (not configurable — keeps config surface small).
- **Alternative considered:** per-VM override. Rejected for now: commit duration is dominated
  by data volume and host I/O, and one global knob suffices; per-VM can be added later without
  breaking changes via config inheritance.

### D4. Intent journal (WAL-lite) in per-VM state

New per-VM state key `commit_in_progress`: a list of intent records, one per disk:

```
{ "disk": "vda",
  "snapshots": ["<oldest>", ...],        # merge set, oldest first
  "base": "/path/to/img.qcow2",
  "started_ts": "20260812T150126" }
```

`IStateManager` gains (BREAKING):

```
set_commit_in_progress(vm_name: str, disk: str, snapshots: list[str], base: str, started_ts: str) -> None
get_commit_in_progress(vm_name: str) -> list[CommitIntent]
clear_commit_in_progress(vm_name: str, disk: str) -> None
```

`CommitIntent` is a frozen dataclass in `qsnap/models/`. `JsonStateManager` persists the list
under the top-level key `commit_in_progress` of `{vm}.json` using the existing atomic
tmp+`os.replace` write. Missing key → empty list (no migration needed). `InMemoryStateManager`
implements the same API.

Protocol in `Core._blockcommit_one_disk`:

1. **Before** invoking the manager: `set_commit_in_progress(...)` (intent is on disk before the
   irreversible operation starts).
2. Manager returns.
   - `outcome="success"` → `set_last_commit_ts`, `remove_snapshot` per merged snapshot, then
     `clear_commit_in_progress` (last, so a crash anywhere still leaves the intent).
   - `outcome="unknown"` → reconciliation (D5); intent cleared only when reconciliation
     finalizes the outcome.
   - `outcome="failure"` (definitive) → clear intent (chain verified unchanged by the manager's
     short-circuit contract), then existing failure classification (MAC/ENOSPC/RuntimeError).

Crash recovery at the start of the next run (inside step 0, alongside deferred operations):
for each intent record of the VM:

- Probe `virsh blockjob` for that disk.
- Job active → keep the intent, defer this disk's commit for this run (WARNING, reason
  `blockjob_active`). The hourly cadence re-checks; no unbounded waiting.
- No job → reconcile (D5 logic): top file gone → late success, write state, clear intent
  (WARNING "commit completed after previous run timed out"); chain unchanged → clear intent
  (WARNING "previous run died during commit attempt; no effect observed").

- **Alternative considered:** reuse the deferred-operations queue for intent. Rejected:
  deferred entries mean "merge still needed"; intent means "merge may be in flight or already
  done" — different semantics, different resolution.

### D5. Reconciliation: reality is the source of truth

New Core helper `_reconcile_commit_outcome(vm_config, disk, base_image, snapshots,
chain_length_before=None) -> ReconcileOutcome` with values `late_success | job_active |
failure | inconclusive`:

1. `virsh blockjob --domain <vm> --path <disk>` (30 s). Active job → `job_active`.
2. Probe failed → `inconclusive`.
3. No job → inspect reality per snapshot in the intent/merge set (oldest first):
   - `os.path.exists(snap.path)` False for a snapshot committed with `--delete` → merged.
   - Backing-chain length (`_get_chain_length`) decreased accordingly.
   - All merge-set files gone and chain shorter → `late_success`.
   - All files present, chain unchanged → `failure` (the job died without effect).
   - Mixed/contradictory → `inconclusive`.

When `chain_length_before` is provided (post-timeout dispatch path — the value captured in
`_blockcommit_one_disk` before invoking the manager), agreement is enforced quantitatively:
`late_success` requires the chain to shrink by exactly the merge-set size, `failure`
requires an unchanged length, any mismatch → `inconclusive`. Step-0 crash recovery passes no
baseline (a crashed run never recorded one) and classifies on file evidence corroborated by
chain measurability.

Handling in `_blockcommit_one_disk`:

- `late_success` → WARNING log ("blockcommit completed after client timeout — state synced"),
  `set_last_commit_ts`, `remove_snapshot` for the merged snapshots, clear intent, continue the
  pipeline (post-commit chain verification still runs).
- `job_active` → `add_deferred_blockcommit(reason="blockjob_active")`, keep intent, skip this
  disk for this run. NOT a VM failure.
- `failure` → clear intent, raise `RuntimeError` (aborts the VM; message includes the hint to
  inspect `virsh blockjob` and libvirtd journal).
- `inconclusive` → `add_deferred_blockcommit(reason="vm_state_unknown")`, keep intent, skip
  the disk. Fail closed: never assume either outcome.

Reconciliation is pure Core logic over `IShell` + `IStateManager` — no module imports, no new
module needed.

### D6. Block-job protocol before chain-mutating operations

Shared Core helper `_probe_blockjob(vm_config, disk) -> str` returning `"none" | "active" |
"error"` (parses `virsh blockjob --domain <vm> --path <disk>`, 30 s; "No current block job" →
`none`; any other output → `active`; failed call → `error`). The existing backup-path probe is
refactored onto this helper (behavior unchanged).

- **Pre-commit** (in `_blockcommit_one_disk`, after planning, before the manager): probe each
  disk to be committed. The probe runs on the live (`virsh`) executor path only: `virsh
  blockjob` errors on inactive domains, so probing the offline (`qemu-img`) path would
  fail-closed-block every legitimate offline commit; the offline path is protected by the D7
  domstate re-check instead (same gating in the deferred-queue drain).
  - `active` + intent record exists for this disk → our probable zombie: reconcile (D5) instead
    of starting a new commit.
  - `active` + no intent → unknown job: defer with WARNING, reason `blockjob_active`.
  - `error` → defer, reason `vm_state_unknown` (fail closed).
  - `none` → proceed.
- **Pre-snapshot** (in `_execute_snapshot_steps`, before `_create_snapshot`, only when the VM
  is running): probe each disk of the VM. Any `active` or `error` → skip snapshot creation for
  this VM this run with a WARNING; the onchange gate stays open, the next run retries. No
  deferred-queue entry (nothing to merge — the snapshot simply waits).
- **Never** issue `virsh blockjob --abort` automatically. Abort is an operator action; the
  WARNING messages name the disk and the job so the operator can act.

### D7. Fail-closed offline race guard

In `_blockcommit_one_disk` (offline path): if the `domstate` re-check fails
(`recheck.success is False`), Core SHALL defer all candidates with reason `vm_state_unknown`
and return — it MUST NOT proceed with `qemu-img commit` when the VM state is unknown. The
existing defer-on-non-shut-off behavior is unchanged. The same immediate re-check guards the
deferred-queue drain path (before the intent write), so a VM that boots between planning and
the drain commit can never be written under.

### D8. Dynamic bound for `_find_broken_chain_file`

Bound = `max(64, measured_chain_length + 2)`, where measured length comes from the failing
scan's parsed chain when available, else from the state snapshot count + 8. The walk therefore
always reaches the base of any chain qsnap itself can describe.

### D9. Observability

- INFO before the manager call: `[blockcommit] {vm}/{disk}: committing {n} snapshot(s) into
  {base} (mode={effective_mode}, timeout={timeout}s)`.
- Heartbeat every 60 s from `BlockCommitManager` via `on_heartbeat`: `[blockcommit] {vm}/{disk}:
  still merging {snapshot} into base ({elapsed}s elapsed)`.
- INFO/WARNING lines for every reconciliation outcome (D5) and for intent recovery (D4).
- `QemuImgCommitManager` logs the same intent line; no heartbeat needed (offline commits are
  short; the hard timeout still applies).

## Risks / Trade-offs

- [Extra virsh probe calls per run (1 per disk pre-commit, pre-snapshot, recovery)] → each is
  a 30 s-bounded, millisecond-fast read-only call; acceptable overhead for correctness.
- [Late-success misclassification if an external actor deleted files] → classification requires
  BOTH the file check and the chain-length check to agree; any contradiction → `inconclusive`
  → fail-closed deferral.
- [Intent journal left behind if qsnap never runs again] → harmless inert data; surfaced by
  the WARNING on the next run. Reporting in `qsnap check` is a follow-up.
- [Reader threads in `run_with_heartbeat`] → daemon threads, joined with bounded wait after
  process exit; captured output is bounded by the child's output volume (small for virsh).
- [BREAKING ABC changes (`IShell`, `IStateManager`)] → contract tests parametrize over all
  implementations; mocks updated in the same change; no external consumers of these ABCs exist
  outside the repo.
- [`blockcommit_timeout` default lowered 3600 → 1800] → a legitimately slow large commit now
  becomes `unknown` at 30 min and is reconciled instead of failing; reconciliation turns a
  completed-but-slow job into `late_success`, so the lower default is safe. Operators with
  huge dirty deltas can raise the option.
- [Pre-snapshot probe defers snapshots while a zombie job lingers] → fail-closed by design;
  hourly cadence retries; WARNING makes it visible.

## Migration Plan

1. Single release; no data migration. Old state files lack `commit_in_progress` → read as
   empty list.
2. `blockcommit_timeout` absent from existing configs → default 1800 s. Documented in
   `qsnap.toml.example`.
3. Rollback = downgrade: old code ignores the unknown `commit_in_progress` key on read and
   drops it on next save (equivalent to today's blindness — no corruption).
4. Interface breakage is contained: all `IShell`/`IStateManager` implementations live in this
   repo (`SubprocessShell`, `JsonStateManager`, mocks).

## Open Questions

All prior open questions are resolved by this design:

1. Zombie policy → defer + reconcile on next run; never auto-abort; no unbounded waiting.
2. Default timeout → 1800 s.
3. WAL scope → commit path only; backup world is a follow-up.
4. `check` locking → unchanged (read-only commands stay unlocked); intent visibility in
   `check` is a follow-up.
