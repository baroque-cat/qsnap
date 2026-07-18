"""Contract test: IBucketFullStrategy ABC defines single abstract method."""

from __future__ import annotations

import pytest

from qsnap.interfaces.bucket_strategy import IBucketFullStrategy
from qsnap.modules.backup.bucket_strategy import BucketFullStrategy


def test_ibucketfullstrategy_is_abstract():
    """IBucketFullStrategy is an ABC that cannot be instantiated directly.

    Verifies that:
    - The class has __abstractmethods__ set.
    - ``should_create_full`` is in the abstract methods.
    - Instantiating the ABC directly raises TypeError.
    - ``BucketFullStrategy`` is a concrete subclass that CAN be instantiated.
    """
    # IBucketFullStrategy is an ABC.
    assert hasattr(IBucketFullStrategy, "__abstractmethods__")

    # The single abstract method is ``should_create_full``.
    assert "should_create_full" in IBucketFullStrategy.__abstractmethods__

    # Cannot instantiate the ABC directly.
    with pytest.raises(TypeError):
        IBucketFullStrategy()  # type: ignore[abstract]

    # BucketFullStrategy is a concrete subclass.
    strategy = BucketFullStrategy()
    assert isinstance(strategy, IBucketFullStrategy)
