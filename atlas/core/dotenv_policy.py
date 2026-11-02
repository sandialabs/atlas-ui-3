"""Single switch for whether Atlas reads ``.env`` files.

Atlas reads a ``.env`` file in three places: ``atlas/main.py`` (a pre-import
peek at ``FEATURE_SUPPRESS_LITELLM_LOGGING`` plus ``load_dotenv``), and the
``AppSettings`` dotenv source. Third-party imports (``litellm`` calls a bare
``load_dotenv()``) read one too.

python-dotenv already defines a standard opt-out: when
``PYTHON_DOTENV_DISABLED`` is set to a truthy value, ``load_dotenv`` is a
no-op. This module applies the same switch, with the same parsing, to the
``.env`` reads Atlas performs itself without ``load_dotenv``, so one variable
turns off every ``.env`` read in the process and in any subprocess that
inherits the environment.

The backend test harness (``atlas/tests/conftest.py``) sets it so that a
checkout-local or ancestor-directory ``.env`` cannot change test results
(issue #1149). Nothing in Atlas sets it at runtime: with the variable unset,
behavior is unchanged.
"""

import os

DOTENV_DISABLED_ENV_VAR = "PYTHON_DOTENV_DISABLED"

# Mirrors python-dotenv's ``_load_dotenv_disabled`` (dotenv/main.py), so the
# Atlas-owned reads and ``load_dotenv`` always agree on the same value.
_TRUTHY = frozenset({"1", "true", "t", "yes", "y"})


def dotenv_loading_disabled() -> bool:
    """Return True when ``PYTHON_DOTENV_DISABLED`` is set to a truthy value."""
    value = os.environ.get(DOTENV_DISABLED_ENV_VAR)
    if value is None:
        return False
    return value.casefold() in _TRUTHY
