"""Boot-time kernel capability probes.

Code Executor v2 refuses to start unless:

* The kernel exposes the Landlock syscalls (CONFIG_SECURITY_LANDLOCK,
  upstream since 5.13).
* Unprivileged user namespaces + network namespaces can be created
  (some hardened kernels disable this).

These probes are deliberately read-only and run a one-shot child
process so they cannot leave the parent namespaced.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass


_SYS_LANDLOCK_CREATE_RULESET = 444


def _libc():
    lib = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(lib, use_errno=True)


def is_landlock_supported() -> bool:
    """Ask the kernel for the highest Landlock ABI; non-negative => supported."""
    try:
        libc = _libc()
        libc.syscall.restype = ctypes.c_long
        rc = libc.syscall(
            _SYS_LANDLOCK_CREATE_RULESET,
            None,
            ctypes.c_size_t(0),
            ctypes.c_uint32(1),
        )
        if rc < 0:
            err = ctypes.get_errno()
            if err in (errno.ENOSYS, errno.EOPNOTSUPP):
                return False
            return False
        return True
    except Exception:
        return False


def _userns_netns_probe_script() -> str:
    """Probe must succeed at the full sequence we use at runtime:

    unshare(USER|NET) -> setgroups(deny) -> uid_map write -> gid_map write.
    Hosts with ``kernel.apparmor_restrict_unprivileged_userns=1`` (Ubuntu
    24.04+) allow ``unshare`` itself but block the uid_map write that
    follows. We must catch that here so the server refuses to start.
    """
    return textwrap.dedent(
        """
        import ctypes, os, sys
        CLONE_NEWUSER = 0x10000000
        CLONE_NEWNET  = 0x40000000
        libc = ctypes.CDLL('libc.so.6', use_errno=True)
        if libc.unshare(CLONE_NEWUSER | CLONE_NEWNET) != 0:
            sys.exit(1)
        real_uid, real_gid = os.getuid(), os.getgid()
        try:
            with open('/proc/self/setgroups', 'w') as f:
                f.write('deny')
        except OSError:
            pass
        try:
            with open('/proc/self/uid_map', 'w') as f:
                f.write(f'{real_uid} {real_uid} 1\\n')
            with open('/proc/self/gid_map', 'w') as f:
                f.write(f'{real_gid} {real_gid} 1\\n')
        except OSError:
            sys.exit(2)
        sys.exit(0)
        """
    ).strip()


@dataclass(frozen=True)
class KernelCapabilities:
    landlock: bool
    user_and_net_namespace: bool

    @property
    def all_supported(self) -> bool:
        return self.landlock and self.user_and_net_namespace


def can_create_user_and_net_namespace() -> bool:
    """Probe in a child process so the parent isn't unshared.

    Some hardened kernels disable unprivileged user namespaces via
    ``/proc/sys/kernel/unprivileged_userns_clone`` or limit them via
    ``/proc/sys/user/max_user_namespaces``. We detect by trying.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _userns_netns_probe_script()],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def probe_kernel() -> KernelCapabilities:
    return KernelCapabilities(
        landlock=is_landlock_supported(),
        user_and_net_namespace=can_create_user_and_net_namespace(),
    )
