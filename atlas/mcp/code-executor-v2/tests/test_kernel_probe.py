"""Kernel probe smoke tests.

These tests are environment-dependent — they describe what the host
should support, not what the code should fake. On a kernel without
Landlock or unprivileged userns the probe must return False, not
crash.
"""
from sandbox.kernel_probe import (
    can_create_user_and_net_namespace,
    is_landlock_supported,
    probe_kernel,
)


def test_landlock_returns_bool():
    assert isinstance(is_landlock_supported(), bool)


def test_userns_returns_bool():
    assert isinstance(can_create_user_and_net_namespace(), bool)


def test_probe_aggregates():
    caps = probe_kernel()
    assert caps.all_supported == (
        caps.landlock and caps.user_and_net_namespace
    )
