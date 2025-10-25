import sys
from pathlib import Path

# Ensure the backend root is on sys.path for absolute imports like 'infrastructure.*'
backend_root = Path(__file__).resolve().parents[1]
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))
