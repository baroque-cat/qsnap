# Tasks: atomic-backup-checkpoints

Reference: `proposal.md` (why), `design.md` (how, decisions D1–D6), `specs/` (requirements), `test-plan.md` (verification).

## 1. Git & Environment

- [x] 1.1 Create a new git branch for this change: `git checkout -b atomic-backup-checkpoints`
- [x] 1.2 Run the full test suite to establish a passing baseline before making changes: `poetry run pytest tests/ -m "not integration and not stress and not e2e"`

## 2. NBD Utilities (`qsnap/utils/nbd.py`)

- [x] 2.1 Add `write_checkpoint_xml(checkpoint_name: str) -> Path` (design D5): writes `<domaincheckpoint><name>{checkpoint_name}</name></domaincheckpoint>` to a temp file (prefix `qsnap-checkpoint-`, suffix `.xml`), mirroring `write_backup_xml()`. Spec: `backup-provider` / "Checkpoint XML written for atomic creation".
- [x] 2.2 Extend `nbd_full_export(..., checkpoint_name: str | None = None)` (design D5): when non-None, call `write_checkpoint_xml()` and pass its path as the **third positional argument** to `virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>`; clean up the checkpoint XML temp file in the `finally` block alongside the socket. When None, behavior is byte-identical to today (file-copy path). Spec: `backup-provider` / "NBD pull-model backup via virsh backup-begin".
- [x] 2.3 Raise the libvirt capability gate in `is_libvirt_new_enough()` from `(6, 0)` to `(7, 2)` and update its docstring to cite the libvirt incremental-backup knowledge-base requirement (design D6). Spec: `nbd-bitmap-backup` / "Libvirt version check for NBD API".

## 3. BitmapBackupProvider (`qsnap/modules/backup/bitmap.py`)

- [x] 3.1 Add checkpoint-name generation helper producing `qsnap-{target_hash}-{yyyymmddTHHMMSS}` (local time, second resolution, design D2 — the same clock used for snapshot naming, as required by design D2 and the `nbd-bitmap-backup` spec). No legacy format is ever generated for new checkpoints.
- [x] 3.2 Rework `list_checkpoints()` / prior discovery into newest-wins (design D3): parse the trailing timestamp of every `qsnap-{target_hash}-*` checkpoint — new format `%Y%m%dT%H%M%S` first, legacy format (timestamp embedded in the snapshot-name portion) as fallback — and select the maximum; unparseable names sort oldest and are logged at WARNING.
- [x] 3.3 `create_full_backup()`: generate a checkpoint name and pass it as `checkpoint_name` to `nbd_full_export()` so the baseline is created atomically with the FULL's freeze point (design D1). Spec: `nbd-bitmap-backup` / "Bitmap FULL leaves an atomic checkpoint baseline".
- [x] 3.4 `transfer_missing()`: DELETE the D4 checkpoint-only guard (`bitmap.py:148-157`) and the `_create_checkpoint_only()` pipeline step (design D4). When no prior checkpoint exists, fall through to a full NBD export **with atomic checkpoint** (pre-D4 behavior, hardened).
- [x] 3.5 `transfer_missing()` incremental path: pass `<incremental>{prior}</incremental>` in the backup XML (unchanged) AND a checkpoint XML with a fresh unique name as third arg to `virsh backup-begin` (design D1/D2). No standalone `virsh checkpoint-create-as` anywhere in the success flow.
- [x] 3.6 Rotation (design D3): only after a successful AND verified export, delete ALL `qsnap-{target_hash}-*` checkpoints older than the just-created one (`virsh checkpoint-delete --metadata`, failure → WARNING, non-fatal). On export/verify failure: delete the just-created successor checkpoint best-effort, preserve the prior (retry safety), delete the partial target file.
- [x] 3.7 Remove `_create_checkpoint_only()` if no caller remains; update the module docstring lifecycle (steps 1–8) to the atomic-checkpoint flow; update `IBackupProvider`-facing docstrings referencing post-hoc checkpoints.

## 4. Factory (`qsnap/factory/default.py`)

- [x] 4.1 Update the fallback warning text in `create_backup_provider()` to say `libvirt < 7.2` (the gate function changed in 2.3; keep behavior: fall back to `FileCopyBackupProvider` with WARNING). Spec: `backup-provider` / "Libvirt version check in BitmapBackupProvider".

## 5. Documentation

- [x] 5.1 README "Bitmap Mode" section: document that the first incremental after a FULL contains all blocks written since the FULL **started** (intentional, gap-free chain — not a duplicate transfer); recommend scheduling FULLs during low write activity; state the libvirt >= 7.2 requirement.
- [x] 5.2 Remove/replace any README or docstring statements describing the checkpoint-only first-run optimization (D4) as current behavior.

## 6. Testing

**MANDATORY delegation protocol for the implementing agent:** delegate EACH group below to a separate `@Mr.Tester` subagent, ALL IN PARALLEL (single message). Every delegation prompt MUST contain: (a) the full testing paradigm — pass the absolute path `/home/openuser/vm/qsnap/TESTING.md` with an explicit instruction to read it in full BEFORE writing any test (this is non-negotiable; each tester must obey its categories, markers, mock strategy and mirroring rules); (b) the group's Scope and its Coverage Map rows from `test-plan.md`; (c) the Test Modifications rows (including **REMOVE** actions) relevant to the group — removal of stale tests is a first-class deliverable, not an afterthought; (d) instruction: "Write or fix ONLY these tests. Report source bugs, don't fix them."

- [x] 6.1 Read `test-plan.md` Delegation Groups section
- [x] 6.2 Delegate group `bitmap-unit` to @Mr.Tester (scope: `tests/modules/backup/test_bitmap.py` — 28 scenario rows; includes REMOVE of the 4 D4-era tests and MODIFY of ~24 tests asserting post-hoc `checkpoint-create-as` / two-arg `backup-begin` / delete-then-create rotation; MUST include TESTING.md in the prompt)
- [x] 6.3 Delegate group `nbd-utils-unit` to @Mr.Tester (scope: `tests/utils/test_nbd.py` — 5 scenarios: `write_checkpoint_xml`, third-arg `backup-begin`, 7.2 gate boundaries; MUST include TESTING.md in the prompt)
- [x] 6.4 Delegate group `factory-unit` to @Mr.Tester (scope: `tests/factory/test_default.py` — 6 scenarios: version gate 7.2 boundary cases incl. 6.x→fallback, 7.2→bitmap; MUST include TESTING.md in the prompt)
- [x] 6.5 Delegate group `contract-bitmap` to @Mr.Tester (scope: `tests/interfaces/test_backup_provider.py` — verify `BitmapBackupProvider` + `MockBitmapBackupProvider` still satisfy `IBackupProvider`; update mock if it encodes checkpoint-only behavior; MUST include TESTING.md in the prompt)
- [x] 6.6 Delegate group `bitmap-integration` to @Mr.Tester (scope: `tests/integration/test_bitmap_atomic.py` NEW — 7 scenarios incl. writes-during-FULL gap-elimination proof (R1), writes-during-incremental (R2), crash self-healing (R3), legacy checkpoint migration, exactly-one-checkpoint rotation; MODIFY `test_bitmap_integration.py` (5) and `test_nbd_full_backup.py` (3, skip-message 6.0→7.2). Real libvirt 12.5.0 + QEMU 11.0.2 available on this machine — model real conditions per TESTING.md integration rules; MUST include TESTING.md in the prompt)
- [x] 6.7 Review @Mr.Tester reports and fix any source-level bugs discovered
- [x] 6.8 Re-delegate any groups affected by source fixes
- [x] 6.9 Verify all groups pass and coverage matches `test-plan.md` (every spec scenario has a passing test; all REMOVE-listed tests are gone)

## 7. Final Verification

- [x] 7.1 `poetry run ruff check qsnap/ tests/ && poetry run ruff format --check qsnap/ tests/`
- [x] 7.2 `poetry run pyright` (strict mode) — zero new errors
- [x] 7.3 `poetry run pytest tests/ -m "not integration and not stress and not e2e"` — full unit/mock/contract suite green
- [x] 7.4 `poetry run pytest tests/integration/ -m integration` — green on real libvirt
- [x] 7.5 `openspec validate atomic-backup-checkpoints` — no errors
- [x] 7.6 Manual smoke on a real VM: first run leaves FULL + exactly one `qsnap-*` checkpoint; second run produces an incremental whose content reflects writes made during the FULL
