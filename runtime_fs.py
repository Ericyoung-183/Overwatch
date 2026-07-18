"""Small durability primitives shared by Overwatch runtime state writers."""

from __future__ import annotations

import os
import hashlib
import subprocess
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


def canonical_project_root(path: str | Path) -> str:
    """Return one stable identity for a cwd, collapsing Git subdirectories."""
    raw_path = str(path).strip()
    if not raw_path:
        return ""
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(raw_path)))
    if not os.path.isdir(resolved):
        return resolved
    try:
        result = subprocess.run(
            ["git", "-C", resolved, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return resolved
    if result.returncode != 0 or not result.stdout.strip():
        return resolved
    return os.path.realpath(result.stdout.strip())


def project_identity_sha256(path: str | Path) -> str:
    """Hash the canonical project identity without exposing it in state filenames."""
    project_root = canonical_project_root(path)
    if not project_root:
        raise ValueError("project root is required")
    return hashlib.sha256(project_root.encode("utf-8")).hexdigest()
