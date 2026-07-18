"""IBucketFullStrategy — strategy for deciding when to create a FULL backup.

Bucket-driven FULL backup creation is a separate strategy concern, not
orchestration.  ``Core`` delegates the decision to an ``IBucketFullStrategy``
obtained via ``IVMModuleFactory.create_bucket_full_strategy()``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from qsnap.models.config import RetentionPolicy, TargetConfig
from qsnap.models.results import FullBackupInfo


class IBucketFullStrategy(ABC):
    """Decide whether a bucket-driven FULL backup should be created.

    The strategy encapsulates bucket/anchor logic (period keys, F-marked
    anchor buckets, active buckets) so that ``Core`` no longer holds
    private bucket-strategy methods.  It is a stateless worker created
    through ``IVMModuleFactory.create_bucket_full_strategy()``.
    """

    @abstractmethod
    def should_create_full(
        self,
        target: TargetConfig,
        policy: RetentionPolicy,
        all_fulls: list[FullBackupInfo],
        snapshot_ts: datetime,
        now: datetime,
    ) -> tuple[bool, str]:
        """Return ``(True, bucket_level)`` when a new FULL should be created.

        ``bucket_level`` is one of ``"hourly"``, ``"daily"``, ``"weekly"``,
        ``"monthly"``, ``"yearly"``.  Returns ``(False, "")`` when no FULL
        is needed for the given snapshot timestamp.
        """
        ...
