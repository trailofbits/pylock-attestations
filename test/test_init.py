"""Initial testing module."""

import pylock_attestations


def test_version() -> None:
    version = getattr(pylock_attestations, "__version__", None)
    assert version is not None
    assert isinstance(version, str)
