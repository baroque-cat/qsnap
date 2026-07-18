"""Bucket-driven FULL backup strategy tests — moved to new location.

All ``Core._should_create_bucket_full()`` tests have been migrated to
``tests/modules/retention/test_bucket_full_strategy.py`` as pure unit
tests for ``BucketFullStrategy``.  The bucket logic was extracted from
Core's static methods into a separate strategy class following the
``IBucketFullStrategy`` interface (Decision 3).

Core-level integration tests for bucket delegation are now in
``tests/core/test_pipeline.py`` (e.g. ``test_core_delegates_bucket_decision_to_strategy``,
``test_backup_target_passes_full_list_to_strategy``).
"""

from __future__ import annotations

# All tests migrated to:
#   tests/modules/retention/test_bucket_full_strategy.py
#   tests/core/test_pipeline.py
