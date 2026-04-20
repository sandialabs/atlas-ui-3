import os
import sys
import tempfile
from pathlib import Path

# Ensure the atlas package root is on sys.path for absolute imports like 'infrastructure.*'
atlas_root = Path(__file__).resolve().parents[1]
project_root = atlas_root.parent
if str(atlas_root) not in sys.path:
    sys.path.insert(0, str(atlas_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# --- Telemetry isolation -------------------------------------------------
# OpenTelemetry's ``trace.set_tracer_provider`` is first-writer-wins: once
# a provider has been registered (e.g. by ``OpenTelemetryConfig`` during
# app import), later calls are silently ignored. Without the two guards
# below, test-emitted spans flow through the app's real
# ``JSONLSpanExporter`` and pollute ``<project_root>/logs/spans.jsonl``
# with fixtures like ``unit.test`` / ``sidecar.off`` and tool-call spans
# containing MagicMock stand-ins (which later crash the analysis script).
#
#   1. Point ``APP_LOG_DIR`` at a throwaway session directory so that if
#      anything DOES initialize the app logging/telemetry config, its
#      file artifacts stay inside the tmpdir and never touch prod logs.
#   2. Pre-install a minimal TracerProvider before any test module or
#      app-code import runs. This locks in the test provider so later
#      ``OpenTelemetryConfig`` calls become no-ops for the duration of
#      the pytest session.
_TELEMETRY_TMPDIR = tempfile.mkdtemp(prefix="atlas-test-telemetry-")
os.environ.setdefault("APP_LOG_DIR", _TELEMETRY_TMPDIR)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    _test_provider = TracerProvider(
        resource=Resource.create({"service.name": "atlas-tests"})
    )
    trace.set_tracer_provider(_test_provider)
except Exception:  # pragma: no cover — defensive, tests run without OTel too
    pass

# Pre-import critical modules before any test files can replace them with fakes.
# This prevents test pollution where one test file patches sys.modules and other
# tests import the fake instead of the real module.
# See test_capability_tokens_and_injection.py which patches LiteLLMCaller.
import atlas.modules.llm.litellm_caller  # noqa: E402, F401

# Explicitly reference the module to satisfy static analyzers that flag unused imports.
# The import above is intentional: it pre-populates sys.modules with the real module.
_ = atlas.modules.llm.litellm_caller.LiteLLMCaller  # noqa: E402
