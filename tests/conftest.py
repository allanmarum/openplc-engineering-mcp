"""Shared test fixtures and markers."""

import tempfile
from pathlib import Path

import pytest


def _symlinks_supported() -> bool:
    """Windows only allows os.symlink with admin rights or developer mode enabled."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.touch()
            (Path(tmp) / "link").symlink_to(target)
    except OSError:
        return False
    return True


requires_symlinks = pytest.mark.skipif(
    not _symlinks_supported(), reason="creating symlinks requires elevation on this platform"
)
