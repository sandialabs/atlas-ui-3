"""Standalone sandbox launcher for Code Executor v2.

Invoked as::

    python /abs/path/_sandbox_launch_v2.py \
        --workdir <ws> \
        [--mem-mb N] [--cpu-s N] [--fsize-mb N] [--nproc N] \
        [--allow-net] \
        -- <CMD> [ARGS...]

Order of operations in the child (must match):

1. ``unshare(CLONE_NEWUSER | CLONE_NEWNET)`` unless ``--allow-net``.
   The new netns has only a down ``lo`` -- no routes, no DNS.
   The new userns is mapped 1:1 (just enough to allow the netns).
2. ``setrlimit`` for AS / CPU / FSIZE / NPROC.
3. ``prctl(PR_SET_NO_NEW_PRIVS, 1)`` -- prerequisite for unprivileged
   Landlock and good hygiene against setuid escapes.
4. Build a Landlock ruleset:
   * Full R/W under ``--workdir``.
   * R + W on ``/dev`` (so /dev/null, /dev/urandom, /dev/tty work).
   * R + X on the standard system roots: /usr /lib /lib64 /bin /sbin
     /etc /opt /proc /sys, plus the directory containing the target
     binary (resolved before sandboxing).
5. ``landlock_restrict_self``.
6. ``execvp(cmd, argv)``.

This file is intentionally **stdlib-only** so it can be invoked by
absolute path from any cwd, without needing the ``atlas`` package on
``sys.path``. It mirrors ``atlas/modules/process_manager/_sandbox_launch.py``
in style (so reviewers can compare) but adds the network-namespace and
rlimit layers that the agent-portal version does not need.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import os
import resource
import shutil
import sys
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Syscall numbers (x86_64 / aarch64 -- identical on both)
# ---------------------------------------------------------------------------
_SYS_LANDLOCK_CREATE_RULESET = 444
_SYS_LANDLOCK_ADD_RULE = 445
_SYS_LANDLOCK_RESTRICT_SELF = 446

_PR_SET_NO_NEW_PRIVS = 38
_LANDLOCK_RULE_PATH_BENEATH = 1

_CLONE_NEWUSER = 0x10000000
_CLONE_NEWNET = 0x40000000

# Landlock filesystem access bits (ABI v1..v5)
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

_DEV_ACCESS = _READ_ACCESS | _ACCESS_WRITE_FILE

_EXTRA_READ_ROOTS: Tuple[str, ...] = (
    "/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc",
    "/opt", "/proc", "/sys",
)


# ---------------------------------------------------------------------------
# ctypes plumbing
# ---------------------------------------------------------------------------
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
    libc = ctypes.CDLL(lib, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


# ---------------------------------------------------------------------------
# Step 1: namespace isolation
# ---------------------------------------------------------------------------
def _enter_user_and_net_namespace() -> None:
    """unshare(CLONE_NEWUSER|CLONE_NEWNET) and set up identity uid/gid map.

    The fresh netns has only a down ``lo`` device by default. With no
    routes and no DNS, IP traffic is effectively black-holed.
    """
    libc = _libc()
    real_uid = os.getuid()
    real_gid = os.getgid()
    rc = libc.unshare(_CLONE_NEWUSER | _CLONE_NEWNET)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), "unshare(CLONE_NEWUSER|CLONE_NEWNET)")

    # Required by the kernel before writing uid_map: deny setgroups so the
    # unprivileged user cannot drop group memberships and bypass policy.
    try:
        with open("/proc/self/setgroups", "w") as f:
            f.write("deny")
    except OSError:
        # Older kernels may not have setgroups; map writes will surface
        # the failure if so.
        pass
    with open("/proc/self/uid_map", "w") as f:
        f.write(f"{real_uid} {real_uid} 1\n")
    with open("/proc/self/gid_map", "w") as f:
        f.write(f"{real_gid} {real_gid} 1\n")


# ---------------------------------------------------------------------------
# Step 2: rlimits
# ---------------------------------------------------------------------------
def _apply_rlimits(mem_mb: int, cpu_s: int, fsize_mb: int, nproc: int) -> None:
    if mem_mb > 0:
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024,) * 2)
    if cpu_s > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
    if fsize_mb > 0:
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_mb * 1024 * 1024,) * 2)
    if nproc > 0:
        # NPROC is per-real-user. After unshare(CLONE_NEWUSER) only "us"
        # exists in the namespace, so this caps fork bombs reliably.
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
    # Disable core dumps -- a sandboxed process should not leak via cores.
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass


# ---------------------------------------------------------------------------
# Step 3+4+5: Landlock
# ---------------------------------------------------------------------------
def _handled_mask(libc) -> int:
    """Best-effort handled_access mask matching the kernel's ABI."""
    abi = libc.syscall(
        _SYS_LANDLOCK_CREATE_RULESET,
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(1),
    )
    if abi < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), "landlock_create_ruleset(version)")
    mask = (
        _ACCESS_EXECUTE | _ACCESS_WRITE_FILE | _ACCESS_READ_FILE | _ACCESS_READ_DIR
        | _ACCESS_REMOVE_DIR | _ACCESS_REMOVE_FILE | _ACCESS_MAKE_CHAR
        | _ACCESS_MAKE_DIR | _ACCESS_MAKE_REG | _ACCESS_MAKE_SOCK
        | _ACCESS_MAKE_FIFO | _ACCESS_MAKE_BLOCK | _ACCESS_MAKE_SYM
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


def _apply_landlock(workdir: str, extra_read_dirs: List[str]) -> None:
    if not workdir or not os.path.isdir(workdir):
        raise ValueError(f"workdir must be an existing directory: {workdir!r}")

    libc = _libc()
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
        _add_rule(libc, ruleset_fd, "/dev", _DEV_ACCESS, handled)
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--mem-mb", type=int, default=2048)
    parser.add_argument("--cpu-s", type=int, default=30)
    parser.add_argument("--fsize-mb", type=int, default=256)
    parser.add_argument("--nproc", type=int, default=64)
    parser.add_argument(
        "--allow-net",
        action="store_true",
        help="Skip network namespace (used only by git_clone).",
    )
    # The double-dash separator after our flags is consumed by argparse;
    # everything after is the command to exec.
    parser.add_argument("cmd", nargs=argparse.REMAINDER)

    ns = parser.parse_args(argv)
    cmd_argv = ns.cmd
    if cmd_argv and cmd_argv[0] == "--":
        cmd_argv = cmd_argv[1:]
    if not cmd_argv:
        sys.stderr.write("sandbox: no command supplied\n")
        return 2

    # Resolve the target binary BEFORE Landlock locks us out so we can
    # whitelist its containing directory for read+exec. We whitelist:
    #   * dirname(symlink-path)        -- e.g. <venv>/bin
    #   * dirname(symlink-path)/..     -- e.g. <venv>/  (pyvenv.cfg, lib/)
    #   * dirname(realpath)            -- the actual binary install dir
    #   * dirname(realpath)/..         -- where the actual stdlib lives
    extra_read_dirs: List[str] = []
    target = cmd_argv[0]
    resolved = shutil.which(target)
    candidates = []
    if resolved:
        candidates.append(resolved)
        candidates.append(os.path.realpath(resolved))
    elif os.path.sep in target:
        candidates.append(target)
        candidates.append(os.path.realpath(target))
    seen = set()
    for path in candidates:
        d = os.path.dirname(path)
        if d and d not in seen:
            seen.add(d)
            extra_read_dirs.append(d)
        parent = os.path.dirname(d)
        if parent and parent not in seen:
            seen.add(parent)
            extra_read_dirs.append(parent)

    try:
        if not ns.allow_net:
            _enter_user_and_net_namespace()
        _apply_rlimits(ns.mem_mb, ns.cpu_s, ns.fsize_mb, ns.nproc)
        _apply_landlock(ns.workdir, extra_read_dirs)
    except Exception as e:
        sys.stderr.write(f"sandbox setup failed: {e}\n")
        return 1

    # Switch into the workspace so user code's cwd is the workspace.
    try:
        os.chdir(ns.workdir)
    except OSError as e:
        sys.stderr.write(f"sandbox chdir failed: {e}\n")
        return 1

    try:
        os.execvp(cmd_argv[0], cmd_argv)
    except FileNotFoundError as e:
        sys.stderr.write(f"command not found: {e}\n")
        return 127
    except PermissionError as e:
        sys.stderr.write(f"permission denied: {e}\n")
        return 126
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
