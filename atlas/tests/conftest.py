import sys
from pathlib import Path

# Ensure the backend root is on sys.path for absolute imports like 'infrastructure.*'
backend_root = Path(__file__).resolve().parents[1]
project_root = backend_root.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Pre-import critical modules before any test files can replace them with fakes.
# This prevents test pollution where one test file patches sys.modules and other
# tests import the fake instead of the real module.
# See test_capability_tokens_and_injection.py which patches LiteLLMCaller.
import atlas.modules.llm.litellm_caller  # noqa: E402, F401
