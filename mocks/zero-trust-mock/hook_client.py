#!/usr/bin/env python3
"""Atlas-side hook: forward the envelope to the zero-trust policy server.

Install as a PreToolUse (and/or PermissionRequest) hook; see hooks.json in this
directory. Reads the event envelope on stdin, POSTs it to the policy server,
and prints the server's decision verbatim on stdout -- Atlas applies it.

Uses only the standard library: hooks run with a minimal environment
allow-list (PATH, HOME, LANG, USER, ATLAS_CONFIG_DIR, ATLAS_PROJECT_DIR) and
should not depend on the server's site-packages.

Configuration: pass the policy URL (and optionally a timeout in seconds) as
argv from ``hooks.json``::

    "command": ["python3", "${ATLAS_CONFIG_DIR}/hooks/hook_client.py",
                "http://localhost:8099/v1/authorize", "2"]

Hooks receive only an environment allow-list (PATH, HOME, LANG, LC_ALL, USER,
SYSTEMROOT, ATLAS_CONFIG_DIR, ATLAS_PROJECT_DIR), so a ``ZERO_TRUST_URL``
exported next to the server would never reach this process. The env vars below
are honored only for running this script by hand from a shell.

Environment (manual runs only):
  ZERO_TRUST_URL      policy endpoint (default http://localhost:8099/v1/authorize)
  ZERO_TRUST_TIMEOUT  seconds to wait for the policy server (default 2)

Failure behaviour: exit non-zero and let the event's ``on_error`` decide.
PreToolUse and PermissionRequest default to ``deny``, so an unreachable policy
server blocks tool calls rather than silently waving them through -- which is
what "zero trust" has to mean when the decider is down.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:8099/v1/authorize"
DECISIONS = frozenset({"continue", "modify", "deny", "require_approval"})


def main(argv: list) -> int:
    # argv wins (hooks.json controls it); env is the manual-run convenience.
    url = argv[0] if len(argv) > 0 else os.getenv("ZERO_TRUST_URL", DEFAULT_URL)
    timeout = float(argv[1]) if len(argv) > 1 else float(os.getenv("ZERO_TRUST_TIMEOUT", "2"))

    envelope = sys.stdin.read()
    request = urllib.request.Request(
        url,
        data=envelope.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decision = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"zero-trust policy server unreachable: {exc}", file=sys.stderr)
        return 1  # not exit 2: this is a hook *error*, so on_error applies

    # Validate before echoing. Atlas reads unrecognized output as "continue",
    # so a 2xx body that is not a decision -- a captive portal, a proxy error
    # page rendered as JSON, a rolled-back API -- would silently become
    # permission to proceed. Treat it as a hook error and let on_error decide.
    if not isinstance(decision, dict) or decision.get("decision") not in DECISIONS:
        print(f"zero-trust policy server returned no usable decision: {decision!r}", file=sys.stderr)
        return 1

    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
