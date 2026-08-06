# Tasks — fault-tolerance-hardening

References: `proposal.md` (why/what), `design.md` (decisions D1–D16), `specs/` (11 delta
specs — normative requirements), `test-plan.md` (coverage map, delegation groups, tests
to delete, integration updates). All code, comments, and documentation MUST be written
in English. Follow AGENTS.md paradigm: DI with ABC interfaces, result objects (never
exceptions for expected failures), frozen config dataclasses, all external commands via
`IShell`, modules stateless, Core the only coordinator.

## 1. Git & Environment

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b fault-tolerance-hardening`
- [ ] 1.2 Verify all existing tests pass before starting: run the full test suite
      (`poetry run pytest tests/ -m "not integration and not stress and not e2e"`)

## 2. Task 1 — ENOSPC fault handling and auto-resume (specs: enospc-fault-handling, state-recovery, deferred-operations, core-orchestrator, cli-interface, config-model, config-parsing; design D1–D7, D16)

- [x] 2.1 Add pure helper `is_space_error(error: str | None) -> bool` to
      `qsnap/utils/retry.py` (case-insensitive: "no space left on device",
      "disk quota exceeded"; None/empty → False; no I/O; re-export per module
      convention). Spec: enospc-fault-handling "Space-error classification helper".
- [x] 2.2 Harden `JsonStateManager._save()` (`qsnap/state/json_manager.py`): wrap
      write + `os.replace` + rotation in `try/except OSError` → CRITICAL log naming path
      and OS error → re-raise `RuntimeError`. Never delete/rename state files in the
      handler. Spec: state-recovery "State write survives ENOSPC…".
- [x] 2.3 Add free-space gate config fields: `GlobalConfig.free_space_check: str =
      "strict"`, `free_space_reserve: int = 0`, `free_space_factor: float = 1.0`
      (`qsnap/models/config.py`); parse + validate in `qsnap/config/facade.py`
      (enum strict|warn|off → `ConfigError` naming valid values; reserve >= 0;
      factor >= 1.0); global→VM inheritance. Specs: config-model "GlobalConfig
      free-space gate fields", config-parsing "ConfigFacade parses and validates
      free-space gate fields".
- [x] 2.4 Create `qsnap/utils/space.py`: `estimate_full_size(shell, source_path) ->
      int | None` (sum `actual-size` over backing chain via `qemu-img info
      --force-share`), `estimate_incremental_size(shell, source_path) -> int | None`
      (active layer `actual-size`), `check_free_space(target_dir, estimate, reserve,
      factor)` using `shutil.disk_usage`; add frozen `SpaceCheckResult` to
      `qsnap/models/results.py`. Undecidable estimate → `None`. Spec:
      enospc-fault-handling "Proactive free-space gate before transfers"; design D5.
- [x] 2.5 Implement per-target suspension in `Core._backup_target` /
      `_execute_backup_steps` (`qsnap/core/__init__.py`): transfer failures classified
      by `is_space_error` suspend ONLY the affected target (skip its remaining
      disks/transfers, CRITICAL log), continue with next target; retention + cleanup
      STILL run for the suspended target. Non-space failures keep `BackupAbortError`.
      Verification failures are never space-classified. Track space-limited targets in
      a run-scoped set. Specs: enospc-fault-handling "Per-target suspension…",
      "Never-delete-on-ENOSPC invariant"; core-orchestrator "VM-level failure
      isolation", "BackupAbortError marks backup-stage failures", "Backup target
      pipeline with gate/retention separation" (all MODIFIED).
- [x] 2.6 Wire the proactive gate into `Core._backup_target` before each FULL /
      incremental transfer: strict → suspension path without attempting the transfer;
      warn → WARNING + proceed; off → skip; undecidable estimate → WARNING + proceed.
      Dry-run: prediction entry only, no mutation. Specs: enospc-fault-handling
      "Proactive free-space gate before transfers"; core-orchestrator "Proactive
      free-space gate integrated into backup steps" (ADDED).
- [x] 2.7 Blockcommit ENOSPC → deferral: in `Core._blockcommit_one_disk`, commit
      failures classified by `is_space_error` are recorded via
      `add_deferred_blockcommit(..., reason="enospc")` instead of raising
      `RuntimeError`; snapshot state records remain. Existing drain + threshold
      monitoring apply unchanged. Specs: enospc-fault-handling "Blockcommit space
      errors deferred…"; deferred-operations (MODIFIED queue requirement + ADDED
      "Blockcommit space errors deferred instead of aborting").
- [x] 2.8 Exit code & flag: add `EXIT_DISKFULL = 4` to `qsnap/errors.py`; add
      `space_limited: bool = False` to `PipelineResult` (and per-VM tracking in
      `VMRunResult` if needed); set it for reactive ENOSPC, strict-gate rejection,
      enospc deferral, state-write ENOSPC; map to exit 4 in `qsnap/cli/app.py`
      (precedence: 2/3 first, then 4 over 1; non-space backup abort still 10); name
      space-limited targets in the summary; document exit 4 in `--help` epilog.
      Specs: cli-interface "Exit codes" (MODIFIED); core-orchestrator "Space-limited
      flag wired into PipelineResult" (ADDED).

## 3. Task 2 — Atomic multi-disk snapshots with quiesce on all disks (specs: snapshot-provider, quiesce-snapshot, post-creation-validation, core-orchestrator; design D8–D12)

- [x] 3.1 Add frozen dataclass `SnapshotSpec(disk: str, name: str, path: Path)` to
      `qsnap/models/` (results or config module) with `__all__` re-export. Design D8.
- [x] 3.2 **BREAKING**: add `create_multi(vm_config: VMConfig, specs:
      Sequence[SnapshotSpec], quiesce: bool) -> list[SnapshotResult]` abstract method
      to `ISnapshotProvider` (`qsnap/interfaces/snapshot.py`). Keep single-disk
      `create()` unchanged. Spec: snapshot-provider "Batch multi-disk snapshot
      creation via create_multi".
- [x] 3.3 Implement `ExternalSnapshotProvider.create_multi`
      (`qsnap/modules/snapshot/external.py`): ONE `virsh snapshot-create-as --domain
      <vm> --name <batch_name> --diskspec <disk>,file=<path>,snapshot=external` (per
      disk) `--disk-only --atomic --no-metadata [--quiesce]`; lock-retry loop (3
      attempts, backoff 2s/4s) wraps the WHOLE call; per-file post-validation
      (existence/qcow2/virtual-size/actual-size≤50%/corrupt/backing-filename); ONE
      `virsh domblklist` pivot check for all disks; timeouts 180s (quiesce) /
      `120 + 30 × (N − 1)`s (non-quiesce); on any failure best-effort `rm -f` of all
      batch files. Return one `SnapshotResult` per spec, in spec order. Specs:
      snapshot-provider (ADDED requirements), quiesce-snapshot (MODIFIED + ADDED),
      post-creation-validation (MODIFIED — batch semantics).
- [x] 3.4 Rewrite `Core._create_snapshot` (`qsnap/core/__init__.py`): generate all
      per-disk names/paths first (`{vm}.{ts}_{disk}_{6hex}.qcow2`,
      `vm_config.snapshot_dir_for(disk)`), call `create_multi` ONCE with
      `quiesce=vm_config.snapshot_quiesce`; record state ONLY on full batch success;
      any failure → record nothing + `RuntimeError` (VM abort). DELETE the
      `index == 0` quiesce hack. Single-disk VMs use the same path. Spec:
      core-orchestrator "Per-disk snapshot creation with configured disk list"
      (MODIFIED).
- [x] 3.5 Update `MockSnapshotProvider` (`tests/mocks/mock_modules.py`) with
      `create_multi` returning one successful `SnapshotResult` per spec in order plus
      failure-injection support — REQUIRED immediately because the ABC change breaks
      mock instantiation (the `mocks-contracts` test group later adds contract/validity
      tests on top; coordinate via the group's report). Design D8.

## 4. Task 3 — snapshot_preserve_min default 0 → 48 (specs: config-model, config-parsing, snapshot-preserve-min; design D13–D14)

- [x] 4.1 Change `GlobalConfig.snapshot_preserve_min` default `0 → 48` in
      `qsnap/models/config.py`; update the field docstring (48 = active floor;
      explicit 0 disables; floor dominates when > chain_length). Specs: config-model
      (MODIFIED "GlobalConfig default values", "GlobalConfig snapshot_preserve_min
      field"); snapshot-preserve-min (MODIFIED filter requirement).
- [x] 4.2 Verify inheritance and validation unchanged: `VMConfig.snapshot_preserve_min
      = None` resolves to the global value; `>= 0` validation intact; explicit
      `snapshot_preserve_min = 0` still disables the floor. Spec: config-parsing
      (ADDED scenarios "snapshot_preserve_min default resolves to 48", "Explicit zero
      preserve_min still honored").

## 5. Documentation & examples

- [x] 5.1 Update `qsnap.toml.example`: document `snapshot_preserve_min = 48` default
      with the interaction note (floor dominates `snapshot_chain_length = 24`;
      opt-out via explicit 0) and the new `free_space_check` / `free_space_reserve` /
      `free_space_factor` options with examples.
- [x] 5.2 Confirm CLI `--help` epilog and any README/exit-code documentation list
      exit code 4 (disk-full) and its auto-resume meaning.

## 6. Testing

MANDATORY HANDOFF RULE: the lead programmer agent executing this section MUST pass the
document `/home/openuser/vm/qsnap/TESTING.md` (the project's testing philosophy,
directory structure, categories, and paradigm — tests mirror the production hierarchy,
zero real I/O in unit tests, custom ABC mocks, markers, `poetry run pytest`) to EVERY
@Mr.Tester subagent it delegates to, together with the group's scope, its Coverage Map
rows from `test-plan.md`, and the change artifacts (`openspec/changes/
fault-tolerance-hardening/{proposal.md,design.md,specs/}`). No tester may start
without TESTING.md.

- [x] 6.1 Read `test-plan.md`: Delegation Groups, Coverage Map, "Tests To Delete", and
      "Integration/Stress/E2E Test Updates" sections.
- [x] 6.2 Delegate group `utils-state-unit` to @Mr.Tester (scope:
      tests/utils/test_retry.py, tests/utils/test_space.py [NEW],
      tests/state/test_manager.py). Hand off TESTING.md + coverage rows + change
      artifacts. Instruction: write/fix ONLY these tests; report source bugs, do not
      fix them.
- [x] 6.3 Delegate group `snapshot-quiesce-unit` to @Mr.Tester (scope:
      tests/modules/snapshot/test_external.py, tests/models/test_results.py). Hand off
      TESTING.md + coverage rows + change artifacts.
- [x] 6.4 Delegate group `core-pipeline-unit` to @Mr.Tester (scope:
      tests/core/test_enospc_isolation.py [NEW], tests/core/test_pipeline.py,
      tests/core/test_engine.py, tests/core/test_dry_run_prediction.py,
      tests/core/test_deferred.py, tests/core/test_preserve.py). Hand off TESTING.md +
      coverage rows + change artifacts + the "Tests To Delete" entries for
      test_pipeline.py / test_engine.py / test_preserve.py (obsolete partial-recording
      and first-disk-quiesce tests must be deleted/replaced per that list).
- [x] 6.5 Delegate group `config-cli-unit` to @Mr.Tester (scope: tests/config/*,
      tests/cli/*, tests/fixtures/configs/*, qsnap.toml.example assertions). Hand off
      TESTING.md + coverage rows + change artifacts + the default-0 blast-radius
      entries from "Tests To Delete".
- [x] 6.6 Delegate group `mocks-contracts` to @Mr.Tester (scope:
      tests/mocks/mock_modules.py, tests/mocks/mock_config.py,
      tests/mocks/test_mock_factory.py, tests/mocks/test_mock_validity.py,
      tests/interfaces/test_snapshot_provider.py,
      tests/interfaces/test_state_manager.py). Hand off TESTING.md + coverage rows +
      change artifacts. Note: `MockSnapshotProvider.create_multi` already exists from
      task 3.5 — the group verifies/extends it and adds contract parametrization.
- [x] 6.7 Delegate group `integration-stress-e2e` to @Mr.Tester (scope:
      tests/integration/*, tests/stress/* incl. NEW tests/stress/test_enospc.py,
      tests/e2e/*). Hand off TESTING.md + coverage rows + change artifacts + the full
      "Integration/Stress/E2E Test Updates" section — these tests MUST verify the NEW
      behavior (quiesced 2-disk batch, default-48 floor with real blockcommit, ENOSPC
      self-heal/auto-resume/exit 4, gate-before-transfer).
- [x] 6.8 Launch all six delegations IN PARALLEL (single message, six @Mr.Tester
      subagents).
- [x] 6.9 Review all @Mr.Tester reports; fix any source-level bugs discovered in
      `qsnap/` (never let testers patch source); re-delegate affected groups after
      fixes.
- [x] 6.10 Verify: full fast suite passes (`poetry run pytest tests/ -m "not
      integration and not stress and not e2e"`); integration/stress/e2e groups pass
      where libvirt is available; coverage matches `test-plan.md` (every Coverage Map
      row has a passing test; every "Tests To Delete" entry is deleted or renamed as
      prescribed).

## 7. Verification & wrap-up

- [ ] 7.1 Run `ruff check` and `ruff format --check` on all touched files; run
      `pyright` (strict) — zero new errors.
- [ ] 7.2 Run `openspec validate fault-tolerance-hardening` (or the repo's validate
      command) and resolve any findings.
- [ ] 7.3 Manual smoke check of the three behaviors on a scratch VM if libvirt is
      available: (a) ENOSPC on a target suspends only that target and exits 4;
      (b) 2-disk VM with `snapshot_quiesce = true` produces ONE virsh call with two
      `--diskspec`; (c) default config keeps 48 snapshots uncommitted.
- [ ] 7.4 Inspect `git status` / `git diff`; commit per task group with concise
      English messages (only when the user requests commits).
