## Context

qsnap is a QEMU/KVM snapshot and backup orchestration tool running as a systemd timer service. The current architecture follows strict Dependency Injection with ABC interfaces: `Core` orchestrates modules (`ISnapshotProvider`, `IBackupProvider`, `ILifecycleManager`, `IChangeDetector`) through `IVMModuleFactory`. All config is immutable frozen dataclasses. All fallible operations return Result objects. The project has 33 implemented specs and 516 tests.

The pipeline (`Core._execute_pipeline`) runs: pre-flight validation → deferred blockcommit check → change detection → snapshot creation → retention evaluation → blockcommit → backup transfer → backup retention. It handles AppArmor/SELinux denials via deferred operations, uses atomic state writes, and performs post-backup verification at configurable levels.

What's missing: defense against stale partial files from crashed runs, corrupted state file recovery, pre/post-commit chain integrity checks, retry for transient backup failures, and a separate deep-verification circuit that doesn't block the hourly pipeline.

## Goals / Non-Goals

**Goals:**
- Add automatic safety operations (T0/T1) that run on every pipeline invocation: stale file cleanup, state file corruption recovery with rotation, backing chain integrity verification before/after blockcommit
- Add deferred deep verification (T2) triggered on shut-off VM blockcommits only — never blocks live VM pipeline
- Add separate deep-verification circuit (T3) via dedicated systemd timer — completely independent of main pipeline
- Add exponential backoff retry for backup transfers on transient errors
- Extend config model with safety fields — simple operations ON by default, heavy operations OFF by default
- Provide transparency: `qsnap list config` shows effective safety settings (ON/OFF)
- Update `qsnap.toml.example` with all existing-but-hidden fields and all new safety fields

**Non-Goals:**
- `qemu-img check` on EVERY snapshot creation (too expensive — it stays as SHA-256 + metadata verify on the live path)
- Automatic repair of corrupted qcow2 images (`qemu-img check -r all` is manual/operator decision)
- Non-linear snapshot branching/forking (separate future change)
- Prometheus metrics or external monitoring integration (separate future change)
- Parallel backup to multiple targets (separate future change)

## Decisions

### Decision 1: Tiered verification model

**Choice:** Three tiers with clear separation.

| Tier | Cost | When | Examples |
|---|---|---|---|
| T0/T1 | < 1 sec | Every pipeline run | `test -f`, `qemu-img info --backing-chain`, stalecleanup |
| T2 | Minutes | Only when VM is shut off (deferred commit) | `qemu-img check` after blockcommit |
| T3 | Hours | Separate weekly timer | `qemu-img check` on all images |

**Rationale:** The hourly timer must stay fast. `qemu-img check` on a 200GB qcow2 can take 10-20 minutes of random I/O. Putting it in the main pipeline would block all other VMs. By deferring T2 to shutdown and T3 to a separate timer, the fast path stays fast while deep verification still happens.

**Alternatives considered:**
- Everything in the main pipeline with a timeout → rejected: timeout would just skip verification, defeating the purpose. Better to have it as a separate circuit that always completes.
- No deep verification at all → rejected: silent corruption goes undetected.

### Decision 2: State file corruption recovery

**Choice:** On `_load` failure: rename corrupt file to `.broken.<timestamp>`, log CRITICAL, start with empty state. On `_save`: rotate previous versions (`vm.json` → `vm.json.1` → `vm.json.2`).

**Rationale:** Starting with empty state means `onchange` returns `changed=True` (first-run behavior) — safe default, creates one unnecessary snapshot. Empty snapshot list means retention won't prune existing files — need to handle this carefully (retention should still enumerate actual files on disk as a fallback). The `.broken` file is preserved for manual investigation. Rotation provides a recovery path without external backup.

**Why not:** Don't attempt auto-repair of JSON (e.g., truncating to last valid record). The corruption could be anywhere; partial repair is worse than clean start.

### Decision 3: Pre-commit chain integrity verification

**Choice:** Before any blockcommit, call `qemu-img info --backing-chain --output=json` on the active image. Verify: every referenced file exists, all formats are `qcow2`, backing-filename references are consistent, no cycles. On failure: skip blockcommit, log CRITICAL with remediation guidance, do NOT defer.

**Rationale:** `qemu-img info --backing-chain` is a single subprocess call, ~1 second. It catches the most dangerous scenario: someone manually deleted a snapshot file but the chain metadata still references it. blockcommit in this state could lose data. This is not a recoverable error — it needs operator intervention, not deferral.

**Alternative:** Check only file existence without `qemu-img info` → rejected: doesn't catch format corruption or broken references.

### Decision 4: Post-commit light verification

**Choice:** After blockcommit, run `qemu-img info --backing-chain` again and compare chain length. If length didn't decrease → CRITICAL (commit didn't work but `--delete` may have removed the snapshot file). If length decreased by expected amount → OK.

**Rationale:** Same call, ~1 second. Catches the case where `blockcommit --delete` removed the snapshot file but the data merge didn't complete. Cost is negligible, safety gain is significant.

### Decision 5: Retry design

**Choice:** Retry wrapper in Core, not in individual providers. Config at target level: `backup_retry_max` (default 3), `backup_retry_base` (default `"2s"`). Backoff: `base`, `base*2`, `base*4`. Only retry on transient errors identified by stderr patterns: `"Connection refused"`, `"No route to host"`, `"timed out"`, `"broken pipe"`, `"EOF"`. Never retry on: `"No space left on device"`, `"Permission denied"`, qcow2-specific errors.

**Rationale:** Retry is a pipeline-level concern, not a provider concern. Providers return structured `ShellResult`/`BackupResult` objects; Core inspects the error field and decides whether to retry. Target-level config because network reliability varies per target (local USB vs remote NAS).

**Alternatives considered:**
- Retry in each provider → rejected: duplicates logic, harder to test, violates "Core coordinates, modules execute" principle.
- Global retry config → rejected: retry behavior is a property of the target's network, not a global setting.

### Decision 6: Pre-flight cleanup

**Choice:** Add `_preflight_cleanup()` step at the very start of `_validate_environment()`, before any other checks. Actions: (a) remove `*.tmp` and `*.partial` files in `snapshot_dir` and all `target.path` directories, (b) remove `/tmp/qsnap-backup-*.sock` (stale NBD sockets), (c) detect but do NOT remove orphan `.qcow2` files (files not recorded in state) — log WARNING.

**Rationale:** Cleanup must happen before any pipeline operation creates new files — otherwise a `snap.qcow2.tmp` from a crashed run could collide with a new snapshot name. Orphan detection is warning-only because the operator may have created files manually or state may have been corrupted (Decision 2). Auto-deleting orphans could destroy valuable data.

### Decision 7: Transparency via `qsnap list config`

**Choice:** `qsnap list config` gains columns showing per-VM safety settings: `blockcommit_deep_verify` (ON/OFF), `snapshot_deep_verify` (ON/OFF). Global settings shown in a header row. `qsnap check` output includes stale file count and state corruption status.

**Rationale:** If a heavy safety feature is OFF, the operator must be able to confirm this is intentional. Silent default-OFF is a trap. Transparency ensures accountability.

### Decision 8: Config field placement and defaults

**Choice:**
| Field | Level | Default | Rationale |
|---|---|---|---|
| `auto_cleanup` | global | `true` | T0, always safe, no reason to disable |
| `state_backup_count` | global | `2` | T0, always safe |
| `chain_verify_before_commit` | global | `true` | T1, always safe, fast |
| `chain_verify_after_commit` | global | `true` | T1, always safe, fast |
| `deep_check_schedule` | global | `"off"` | T3, must explicitly enable |
| `blockcommit_deep_verify` | VM | `false` | T2, per-VM because disk sizes differ |
| `snapshot_deep_verify` | VM | `false` | T2, per-VM because disk sizes differ |
| `backup_retry_max` | target | `3` | Target-level, reasonable default |
| `backup_retry_base` | target | `"2s"` | Target-level, reasonable default |

**Rationale:** T0/T1 fields are global because they're universally fast and safe — no per-VM variation needed. T2 fields are per-VM because a 500GB production VM and a 10GB test VM have very different `qemu-img check` costs. Target-level retry is a property of network reliability.

## Risks / Trade-offs

- **[Risk] State recovery creates one unnecessary snapshot (onchange returns True)** → Mitigation: one extra snapshot is negligible overhead. The CRITICAL log ensures the operator knows state was lost.
- **[Risk] Pre-commit chain verification failure blocks blockcommit but doesn't self-heal** → Mitigation: this is intentional — a broken chain needs operator intervention. The CRITICAL log includes remediation guidance (check which file is missing/corrupt).
- **[Risk] Post-commit verification may detect the commit didn't work but snapshot file already deleted** → Mitigation: keep snapshot file path in state before commit. If post-commit fails, log the path for manual recovery. In practice, `virsh blockcommit --wait --delete` is reliable — this check is defense-in-depth.
- **[Risk] Retry adds latency to pipeline** → Mitigation: max 3 retries × (2+4+8=14s for `"2s"` base) = ~14s worst case. Configurable per target — set `backup_retry_max=0` to disable. Non-retryable errors (disk full) fail fast.
- **[Risk] Deep verification timer may overlap with main pipeline if schedule is wrong** → Mitigation: separate lockfile for `qsnap-check` or use the same lockfile (preferred — `qsnap check` acquires the lock, blocking `qsnap run` until check completes). Timer set to weekly at 3AM by default.
- **[Trade-off] Orphan `.qcow2` files are warned about but not deleted** → This is intentional — auto-deletion could destroy data. The operator must decide. Future: could add `qsnap cleanup --orphans` as an explicit command.

## Open Questions

- Should `deep_check_schedule = "weekly"` also automatically create/enable the systemd timer, or just serve as documentation? Decision: documentation only. Systemd timer is a separate deployment artifact (`qsnap-check.timer` + `qsnap-check.service`). The config field tells `qsnap check --deep` what schedule it expects to run on (for reporting purposes — e.g., "last deep check: 8 days ago (expected: weekly)").
- Should `chain_verify_before_commit` also be VM-level? Currently global because it's always fast. If a future use case requires per-VM control, it can be extended.
