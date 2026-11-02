"""Single switch for whether Atlas reads ``.env`` files.

python-dotenv defines a standard opt-out: when ``PYTHON_DOTENV_DISABLED`` is
set to a truthy value, ``load_dotenv`` is a no-op. This module applies the
same switch, with the same parsing, to the ``.env`` reads Atlas performs
without ``load_dotenv``.

Readers the switch turns off:

* ``load_dotenv`` callers, natively: ``atlas/main.py`` (import time),
  ``atlas/server_cli.py`` and ``atlas/atlas_chat_cli.py`` (``--env-file`` /
  ``ATLAS_ENV_FILE`` / default env file), and third-party imports such as
  ``litellm``'s bare ``load_dotenv()``.
* Through this module: the ``FEATURE_SUPPRESS_LITELLM_LOGGING`` peek in
  ``atlas/main.py`` and the ``AppSettings`` dotenv source (which logs a
  one-time warning when it skips an existing file).

Deliberately out of scope: ``atlas/init_cli.py`` reads an existing ``.env``
with ``dotenv_values`` only to preserve a signing key while *writing* a new
env file; it never populates the environment or settings.

The backend test harness (``atlas/tests/conftest.py``) sets the switch so that
a checkout-local or ancestor-directory ``.env`` cannot change test results
(issue #1149). Nothing in Atlas sets it. With the variable unset, behavior is
unchanged; a process that already exported it now also skips the
``AppSettings`` ``.env`` source, consistent with ``load_dotenv``.
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
