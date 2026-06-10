"""Minimal Linux Landlock helper for sandboxing a subprocess to a working dir.

Used as a ``preexec_fn`` when launching a subprocess: after fork, before
exec, the child installs a Landlock ruleset that restricts filesystem
access to (a) read+execute on system roots required to actually run a
binary, and (b) full read/write inside the user-supplied working dir.
It also sets PR_SET_NO_NEW_PRIVS so a setuid binary cannot bypass the
sandbox.

Landlock requires a kernel with CONFIG_SECURITY_LANDLOCK enabled
(upstream since 5.13). On unsupported kernels the helper raises
``LandlockUnavailableError`` so the caller can surface a clear error.

Landlock does NOT sandbox network, ioctl, signals, ptrace, /proc, or
similar — it's a filesystem-only control. Treat this as defense in
depth, not a complete sandbox.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
from typing import Iterable, List

# Syscall numbers for x86_64 / aarch64 — these numbers match on both arches.
_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446

_PR_SET_NO_NEW_PRIVS = 38
_LANDLOCK_RULE_PATH_BENEATH = 1

# Landlock filesystem access bits (ABI v1 through v5).
_ACCESS_EXECUTE = 0x1
_ACCESS_WRITE_FILE = 0x2
_ACCESS_READ_FILE = 0x4
_ACCESS_READ_DIR = 0x8
_ACCESS_REMOVE_DIR = 0x10
_ACCESS_REMOVE_FILE = 0x20
_ACCESS_MAKE_CHAR = 0x40
_ACCESS_MAKE_DIR = 0x80
_ACCESS_MAKE_REG = 0x100
_ACCESS_MAKE_SOCK = 0x200
_ACCESS_MAKE_FIFO = 0x400
_ACCESS_MAKE_BLOCK = 0x800
_ACCESS_MAKE_SYM = 0x1000
_ACCESS_REFER = 0x2000        # ABI v2
_ACCESS_TRUNCATE = 0x4000     # ABI v3
_ACCESS_IOCTL_DEV = 0x8000    # ABI v5

_READ_ACCESS = _ACCESS_EXECUTE | _ACCESS_READ_FILE | _ACCESS_READ_DIR

_WORKDIR_ACCESS = (
    _ACCESS_EXECUTE
    | _ACCESS_READ_FILE
    | _ACCESS_READ_DIR
    | _ACCESS_WRITE_FILE
    | _ACCESS_REMOVE_FILE
    | _ACCESS_REMOVE_DIR
    | _ACCESS_MAKE_REG
    | _ACCESS_MAKE_DIR
    | _ACCESS_MAKE_SYM
    | _ACCESS_MAKE_FIFO
    | _ACCESS_MAKE_SOCK
    | _ACCESS_TRUNCATE
)


class LandlockUnavailableError(RuntimeError):
    """Kernel does not support Landlock (no syscall or not compiled in)."""


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _libc():
    lib = ctypes.util.find_library("c")
    if lib is None:
        # Fallback to the usual name
        lib = "libc.so.6"
    return ctypes.CDLL(lib, use_errno=True)


def _best_effort_abi_mask(libc) -> int:
    """Ask the kernel for the highest supported ABI and build a matching mask."""
    # landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION = 1)
    LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
    abi = libc.syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION),
    )
    if abi < 0:
        err = ctypes.get_errno()
        if err in (errno.ENOSYS, errno.EOPNOTSUPP):
            raise LandlockUnavailableError(
                "Landlock syscall unavailable on this kernel"
            )
        raise OSError(err, os.strerror(err), "landlock_create_ruleset(version)")

    mask = (
        _ACCESS_EXECUTE
        | _ACCESS_WRITE_FILE
        | _ACCESS_READ_FILE
        | _ACCESS_READ_DIR
        | _ACCESS_REMOVE_DIR
        | _ACCESS_REMOVE_FILE
        | _ACCESS_MAKE_CHAR
        | _ACCESS_MAKE_DIR
        | _ACCESS_MAKE_REG
        | _ACCESS_MAKE_SOCK
        | _ACCESS_MAKE_FIFO
        | _ACCESS_MAKE_BLOCK
        | _ACCESS_MAKE_SYM
    )
    if abi >= 2:
        mask |= _ACCESS_REFER
    if abi >= 3:
        mask |= _ACCESS_TRUNCATE
    if abi >= 5:
        mask |= _ACCESS_IOCTL_DEV
    return mask


def _add_path_rule(libc, ruleset_fd: int, path: str, allowed: int, handled_mask: int) -> None:
    """Add a path_beneath rule, silently skipping missing paths."""
    try:
        dir_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    try:
        # Only request bits that are in the handled mask; kernel rejects extras.
        allowed &= handled_mask
        if not allowed:
            return
        attr = _PathBeneathAttr(allowed_access=allowed, parent_fd=dir_fd)
        rc = libc.syscall(
            _SYS_LANDLOCK_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint32(_LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(attr),
            ctypes.c_uint32(0),
        )
        if rc != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), f"landlock_add_rule({path})")
    finally:
        os.close(dir_fd)


def restrict_to_workdir(
    workdir: str,
    *,
    extra_read_roots: Iterable[str] = (
        "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc",
        "/opt", "/proc", "/sys", "/dev",
    ),
) -> None:
    """Install a Landlock ruleset restricting FS access to ``workdir``.

    ``extra_read_roots`` allow the child to actually run: system binaries
    need read+execute on /usr, /lib, etc.; name resolution needs /etc;
    some runtimes poke /proc and /sys. Writes are still blocked outside
    ``workdir``.

    Must be called in the child between fork and exec (preexec_fn).
    Raises :class:`LandlockUnavailableError` if the kernel doesn't
    support Landlock.
    """
    if not workdir or not os.path.isdir(workdir):
        raise ValueError(f"workdir must be an existing directory: {workdir!r}")

    libc = _libc()
    libc.syscall.restype = ctypes.c_long

    handled_mask = _best_effort_abi_mask(libc)

    # PR_SET_NO_NEW_PRIVS is required before landlock_restrict_self for
    # unprivileged processes, and is good hygiene regardless.
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), "prctl(PR_SET_NO_NEW_PRIVS)")

    attr = _RulesetAttr(handled_access_fs=handled_mask, handled_access_net=0)
    ruleset_fd = libc.syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )
    if ruleset_fd < 0:
        err = ctypes.get_errno()
        if err in (errno.ENOSYS, errno.EOPNOTSUPP):
            raise LandlockUnavailableError(
                "Landlock not supported by this kernel"
            )
        raise OSError(err, os.strerror(err), "landlock_create_ruleset")

    try:
        # Full R/W inside the working dir.
        _add_path_rule(libc, ruleset_fd, workdir, _WORKDIR_ACCESS, handled_mask)

        # Read+execute-only everywhere required for the binary to run.
        roots: List[str] = []
        for root in extra_read_roots:
            if root in roots:
                continue
            roots.append(root)
        for root in roots:
            _add_path_rule(libc, ruleset_fd, root, _READ_ACCESS, handled_mask)

        rc = libc.syscall(
            _SYS_LANDLOCK_RESTRICT_SELF,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint32(0),
        )
        if rc != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), "landlock_restrict_self")
    finally:
        os.close(ruleset_fd)


def is_supported() -> bool:
    """Quick check that the kernel exposes the landlock syscalls.

    Calls landlock_create_ruleset(version) which does not modify state.
    """
    try:
        libc = _libc()
        libc.syscall.restype = ctypes.c_long
        _best_effort_abi_mask(libc)
        return True
    except LandlockUnavailableError:
        return False
    except OSError:
        return False
