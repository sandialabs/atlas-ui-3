"""Standalone entry point that applies Landlock then execs the target.

Invoked as::

    python /abs/path/to/_sandbox_launch.py WORKDIR CMD [ARGS...]

This file is intentionally self-contained (only stdlib imports) so it
can be run by absolute path without the ``atlas`` package being on
``sys.path`` -- which matters because the child inherits cwd from the
user's selection, not the project root.

We route sandboxed launches through this wrapper instead of using
``preexec_fn`` because uvloop (which backs the FastAPI app) interacts
badly with ``preexec_fn`` and surfaces the failure as
``PermissionError`` during process creation.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
import sys

_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446

_PR_SET_NO_NEW_PRIVS = 38
_LANDLOCK_RULE_PATH_BENEATH = 1

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
_ACCESS_REFER = 0x2000
_ACCESS_TRUNCATE = 0x4000
_ACCESS_IOCTL_DEV = 0x8000

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

_EXTRA_READ_ROOTS = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc",
    "/opt", "/proc", "/sys", "/dev",
)


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
    lib = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(lib, use_errno=True)


def _handled_mask(libc) -> int:
    LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
    abi = libc.syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION),
    )
    if abi < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), "landlock_create_ruleset(version)")
    mask = (
        _ACCESS_EXECUTE | _ACCESS_WRITE_FILE | _ACCESS_READ_FILE | _ACCESS_READ_DIR
        | _ACCESS_REMOVE_DIR | _ACCESS_REMOVE_FILE | _ACCESS_MAKE_CHAR | _ACCESS_MAKE_DIR
        | _ACCESS_MAKE_REG | _ACCESS_MAKE_SOCK | _ACCESS_MAKE_FIFO | _ACCESS_MAKE_BLOCK
        | _ACCESS_MAKE_SYM
    )
    if abi >= 2:
        mask |= _ACCESS_REFER
    if abi >= 3:
        mask |= _ACCESS_TRUNCATE
    if abi >= 5:
        mask |= _ACCESS_IOCTL_DEV
    return mask


def _add_rule(libc, ruleset_fd: int, path: str, allowed: int, handled: int) -> None:
    try:
        dir_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    try:
        allowed &= handled
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


def _apply_landlock(workdir: str, extra_read_dirs=()) -> None:
    if not workdir or not os.path.isdir(workdir):
        raise ValueError(f"workdir must be an existing directory: {workdir!r}")
    libc = _libc()
    libc.syscall.restype = ctypes.c_long
    handled = _handled_mask(libc)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), "prctl(PR_SET_NO_NEW_PRIVS)")

    attr = _RulesetAttr(handled_access_fs=handled, handled_access_net=0)
    ruleset_fd = libc.syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )
    if ruleset_fd < 0:
        err = ctypes.get_errno()
        if err in (errno.ENOSYS, errno.EOPNOTSUPP):
            raise OSError(err, "Landlock not supported by this kernel")
        raise OSError(err, os.strerror(err), "landlock_create_ruleset")
    try:
        _add_rule(libc, ruleset_fd, workdir, _WORKDIR_ACCESS, handled)
        for root in _EXTRA_READ_ROOTS:
            _add_rule(libc, ruleset_fd, root, _READ_ACCESS, handled)
        for extra in extra_read_dirs:
            if extra:
                _add_rule(libc, ruleset_fd, extra, _READ_ACCESS, handled)
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


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: _sandbox_launch WORKDIR CMD [ARGS...]\n")
        return 2

    workdir = sys.argv[1]
    argv = sys.argv[2:]

    # Resolve the target binary BEFORE Landlock is applied so we can
    # whitelist its containing directory for read+exec. Without this,
    # binaries installed outside /usr /bin /sbin /opt (e.g. under
    # ~/.local/bin or ~/.nvm) would fail to exec after restriction.
    extra_read_dirs = []
    target = argv[0]
    resolved = shutil.which(target)
    if resolved:
        extra_read_dirs.append(os.path.dirname(os.path.realpath(resolved)))
    elif os.path.sep in target:
        extra_read_dirs.append(os.path.dirname(os.path.realpath(target)))

    try:
        _apply_landlock(workdir, extra_read_dirs=extra_read_dirs)
    except Exception as e:
        sys.stderr.write(f"sandbox setup failed: {e}\n")
        return 1

    try:
        os.execvp(argv[0], argv)
    except FileNotFoundError as e:
        sys.stderr.write(f"command not found: {e}\n")
        return 127
    except PermissionError as e:
        sys.stderr.write(f"permission denied: {e}\n")
        return 126
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
