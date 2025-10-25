import pkgutil
import sys
from pathlib import Path


# Import all backend python modules to catch import-time errors quickly.
# Skip heavy runtime side effects by not executing app.run, etc.

def iter_backend_modules():
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))

    for module in pkgutil.walk_packages([str(backend_root)], prefix=""):
        name = module.name
        # Skip private and test packages
        if name.startswith("tests"):
            continue
        # Skip MCP servers as they may require external binaries
        if name.startswith("mcp.") or name.startswith("mcp/"):
            continue
        yield name


def test_import_all_backend_modules():
    failed = []
    for name in iter_backend_modules():
        try:
            __import__(name)
        except Exception as e:
            failed.append((name, str(e)))
    assert not failed, f"Import failures: {failed[:5]} (and {max(0, len(failed)-5)} more)"
