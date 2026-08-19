"""Pytest plugin that reorders collected tests, to expose order dependence.

The suite runs in one process in alphabetical order, so a test that leaks state
-- an env var, a module global, a row in a database -- can be masked by the very
ordering CI happens to use. This plugin re-runs the same tests in a different
order so that leak becomes a failure.

Usage (from the ``atlas`` directory)::

    PYTHONPATH=$(git rev-parse --show-toplevel):$(git rev-parse --show-toplevel)/scripts \\
        ATLAS_TEST_ORDER=reverse python -m pytest tests -q -p pytest_test_order

``ATLAS_TEST_ORDER`` accepts:

``reverse``
    Run every test in reverse collection order.
``<integer>``
    Shuffle with that seed. ``ATLAS_TEST_ORDER_SCOPE=module`` (the default)
    keeps each file's tests together and shuffles the files, which is the
    realistic case; ``=test`` shuffles individual tests across files, which is
    harsher and can be noisy for suites with legitimate module-scoped fixtures.

Unset (the default) leaves collection order alone, so adding ``-p`` to a normal
run is a no-op.

Note this reorders *execution*, not *imports*: pytest imports every test module
during collection, in collection order. An import-time leak therefore needs a
pairwise run instead -- the suspect file first, then the file you think it
affects. See docs/developer/test-isolation.md.
"""

import os
import random

import pytest


def pytest_collection_modifyitems(session, config, items):
    order = os.environ.get("ATLAS_TEST_ORDER")
    if not order:
        return

    # Validated before branching on ``order``: a typo'd scope used to pass
    # silently with ATLAS_TEST_ORDER=reverse and fail only with a seed, so the
    # same mistake behaved differently depending on the other variable.
    scope = os.environ.get("ATLAS_TEST_ORDER_SCOPE", "module")
    if scope not in ("module", "test"):
        raise pytest.UsageError(
            f"ATLAS_TEST_ORDER_SCOPE={scope!r} is not 'module' or 'test'"
        )

    if order == "reverse":
        items.reverse()
        print(f"\n[pytest_test_order] reversed {len(items)} tests")
        return

    try:
        seed = int(order)
    except ValueError:
        # UsageError, not SystemExit: in the CI ordering leg a typo must read as
        # "you configured this wrong", not as a failing test run.
        raise pytest.UsageError(
            f"ATLAS_TEST_ORDER={order!r} is not 'reverse' or an integer seed"
        )

    rng = random.Random(seed)
    if scope == "test":
        rng.shuffle(items)
    else:
        by_module = {}
        for item in items:
            by_module.setdefault(item.nodeid.split("::")[0], []).append(item)
        modules = list(by_module)
        rng.shuffle(modules)
        items[:] = [item for module in modules for item in by_module[module]]

    print(f"\n[pytest_test_order] shuffled {len(items)} tests (scope={scope}, seed={seed})")
