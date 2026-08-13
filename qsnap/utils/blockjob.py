"""Pure helper for classifying ``virsh blockjob`` probe output.

Both Core's commit-path probe (:meth:`Core._probe_blockjob`) and
``BitmapBackupProvider``'s pre-backup probe consume this single classifier
so the two call sites cannot drift (blockjob-protocol spec).

The helper is pure — it performs no I/O.  The caller passes the probe
command's captured stdout together with the command's success flag
(``ShellResult.success``); the classifier decides among ``"none"``,
``"active"``, and ``"error"``.
"""

from __future__ import annotations

from typing import Literal

# Markers found in job-describing ``virsh blockjob`` output.  Any one of
# these (case-insensitive) means a block job is currently active.
_ACTIVE_BLOCKJOB_MARKERS: tuple[str, ...] = (
    "block job",
    "block copy",
    "block commit",
    "block pull",
    "active block",
)

BlockjobState = Literal["none", "active", "error"]


def classify_blockjob_output(
    stdout: str,
    *,
    stderr: str = "",
    success: bool = True,
) -> BlockjobState:
    """Classify a ``virsh blockjob`` probe result.

    Both stdout and stderr are inspected: libvirt reports an active job's
    progress on **stderr** for a throttled ``blockcommit`` while stdout may
    carry only a bandwidth line, so a stream-only classifier would
    misclassify a genuinely active job.

    Classification:

    - ``success=False`` (non-zero exit, timeout, missing binary) → ``"error"``.
    - combined stdout+stderr containing ``"No current block job"`` or empty
      combined output → ``"none"``.
    - job-describing output (contains a job-type marker) → ``"active"``.
    - any other non-empty output (unclassifiable) → ``"error"``.
    """
    if not success:
        return "error"
    combined = f"{stdout}\n{stderr}"
    if not combined.strip():
        return "none"
    lowered = combined.lower()
    if "no current block job" in lowered:
        return "none"
    if any(marker in lowered for marker in _ACTIVE_BLOCKJOB_MARKERS):
        return "active"
    return "error"
