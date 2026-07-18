"""Small durability primitives shared by Overwatch runtime state writers."""

from __future__ import annotations

import os
from pathlib import Path


def ensure_private_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return directory


def fsync_directory(path: str | Path) -> None:
    descriptor = os.open(
        Path(path),
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
