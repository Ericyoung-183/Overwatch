"""Atomic compare-and-swap helpers for installer-owned config files."""

from __future__ import annotations

import ctypes
import os
import platform
import stat
import tempfile
from pathlib import Path

from runtime_fs import fsync_directory


class ConfigConflictError(RuntimeError):
    pass


def reject_symlink(path: str | Path) -> Path:
    target = Path(path)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return target
    if stat.S_ISLNK(metadata.st_mode):
        raise ConfigConflictError(f"refusing symbolic-link config: {target}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigConflictError(f"config is not a regular file: {target}")
    return target


def stage_bytes(path: str | Path, content: bytes, mode: int) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temporary)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def _preserve_displaced(displaced: Path, target: Path) -> Path:
    descriptor, recovery_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.overwatch-recovery.",
        suffix=".bak",
    )
    os.close(descriptor)
    recovery = Path(recovery_name)
    try:
        _atomic_rename(displaced, recovery, exchange=True)
        displaced.unlink(missing_ok=True)
        fsync_directory(target.parent)
        return recovery
    except BaseException:
        recovery.unlink(missing_ok=True)
        raise


def _write_recovery_bytes(target: Path, content: bytes, mode: int) -> Path:
    descriptor, recovery_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.overwatch-recovery.",
        suffix=".bak",
    )
    recovery = Path(recovery_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(target.parent)
        return recovery
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        recovery.unlink(missing_ok=True)
        raise


def _quarantine_path(target: Path) -> Path:
    descriptor, quarantine_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.overwatch-rollback.",
        suffix=".bak",
    )
    os.close(descriptor)
    quarantine = Path(quarantine_name)
    quarantine.unlink()
    return quarantine


def _atomic_rename(source: Path, destination: Path, *, exchange: bool) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    system = platform.system()
    if system == "Darwin":
        rename = libc.renameatx_np
        flag = 0x00000002 if exchange else 0x00000004
    elif system == "Linux" and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        flag = 2 if exchange else 1
    else:
        raise ConfigConflictError(
            "atomic config replacement is unsupported on this platform"
        )
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(
        -2,
        os.fsencode(source),
        -2,
        os.fsencode(destination),
        flag,
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)
    fsync_directory(destination.parent)


def commit_staged(
    path: str | Path,
    staged: str | Path,
    *,
    expected_original: bytes | None,
    expected_mode: int | None,
) -> Path | None:
    """Atomically install staged bytes and return the displaced original path."""
    target = reject_symlink(path)
    staged_path = reject_symlink(staged)
    staged_bytes = staged_path.read_bytes()
    staged_mode = stat.S_IMODE(staged_path.stat().st_mode)
    if target.exists():
        _atomic_rename(staged_path, target, exchange=True)
        displaced_matches = (
            expected_original is not None
            and staged_path.read_bytes() == expected_original
            and (
                expected_mode is None
                or stat.S_IMODE(staged_path.stat().st_mode) == expected_mode
            )
        )
        if not displaced_matches:
            _atomic_rename(staged_path, target, exchange=True)
            recovery = _preserve_displaced(staged_path, target)
            raise ConfigConflictError(
                "refusing to replace concurrently modified config; "
                f"post-exchange bytes preserved at recovery: {recovery}"
            )
        if (
            target.read_bytes() != staged_bytes
            or stat.S_IMODE(target.stat().st_mode) != staged_mode
        ):
            recovery = _preserve_displaced(staged_path, target)
            raise ConfigConflictError(
                "config changed immediately after atomic replacement; "
                f"external edit preserved at {target}, original preserved at {recovery}"
            )
        return staged_path
    if expected_original is not None:
        raise ConfigConflictError(f"config disappeared before replacement: {target}")
    try:
        _atomic_rename(staged_path, target, exchange=False)
    except FileExistsError as exc:
        raise ConfigConflictError(
            f"config appeared before no-replace commit: {target}"
        ) from exc
    if target.read_bytes() != staged_bytes:
        raise ConfigConflictError(
            f"config changed immediately after atomic creation: {target}"
        )
    return None


def rollback_commit(
    path: str | Path,
    displaced: Path | None,
    *,
    expected_current: bytes,
    expected_current_mode: int | None = None,
) -> None:
    """Restore one committed file only if no external writer changed it."""
    target = reject_symlink(path)
    current_mismatch = (
        not target.is_file()
        or target.read_bytes() != expected_current
        or (
            expected_current_mode is not None
            and stat.S_IMODE(target.stat().st_mode) != expected_current_mode
        )
    )
    if current_mismatch:
        if displaced is not None:
            recovery = _preserve_displaced(reject_symlink(displaced), target)
            raise ConfigConflictError(
                "external edit preserved during rollback; "
                f"original preserved at {recovery}"
            )
        raise ConfigConflictError(f"external edit preserved during rollback: {target}")
    if displaced is None:
        quarantine = _quarantine_path(target)
        try:
            _atomic_rename(target, quarantine, exchange=False)
        except BaseException:
            raise
        quarantined_matches = (
            quarantine.read_bytes() == expected_current
            and (
                expected_current_mode is None
                or stat.S_IMODE(quarantine.stat().st_mode) == expected_current_mode
            )
        )
        if not quarantined_matches:
            try:
                _atomic_rename(quarantine, target, exchange=False)
            except OSError as exc:
                raise ConfigConflictError(
                    "external edit preserved during rollback at "
                    f"{quarantine}; another config is active at {target}"
                ) from exc
            raise ConfigConflictError(
                f"external edit preserved during rollback: {target}"
            )
        fsync_directory(target.parent)
        return
    displaced = reject_symlink(displaced)
    original_bytes = displaced.read_bytes()
    original_mode = stat.S_IMODE(displaced.stat().st_mode)
    _atomic_rename(displaced, target, exchange=True)
    target_restored = (
        target.read_bytes() == original_bytes
        and stat.S_IMODE(target.stat().st_mode) == original_mode
    )
    displaced_matches = (
        displaced.read_bytes() == expected_current
        and (
            expected_current_mode is None
            or stat.S_IMODE(displaced.stat().st_mode) == expected_current_mode
        )
    )
    if not target_restored:
        recovery = _write_recovery_bytes(target, original_bytes, original_mode)
        raise ConfigConflictError(
            "external edit preserved during rollback; "
            f"original preserved at {recovery}"
        )
    if not displaced_matches:
        raise ConfigConflictError(
            f"external edit to displaced config preserved during rollback: {displaced}"
        )
    displaced.unlink(missing_ok=True)
    fsync_directory(target.parent)
