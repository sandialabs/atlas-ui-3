import os
import sys
import subprocess
from pathlib import Path


def run_subprocess(code: str, cwd: Path):
    env = os.environ.copy()
    # Simulate production run via agent_start.sh: run from backend dir without relying on PYTHONPATH
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc


def test_backend_dir_imports_work_without_project_root_in_path():
    """
    Ensure imports work when running from the backend directory (the supported run mode),
    without requiring the project root on PYTHONPATH.
    This mirrors `bash agent_start.sh` which ends up running uvicorn from ./backend.
    """
    backend_dir = Path(__file__).resolve().parents[1]

    code = (
        "import main; "
        "from atlas.modules.config import ConfigManager; "
        "cm=ConfigManager(); "
        "_ = cm.llm_config; _ = cm.mcp_config; _ = cm.rag_mcp_config; "
        "print('OK')"
    )

    proc = run_subprocess(code, backend_dir)

    # Helpful diagnostics on failure
    if proc.returncode != 0:
        print("STDOUT:\n" + proc.stdout)
        print("STDERR:\n" + proc.stderr)

    assert proc.returncode == 0, "Subprocess failed to import and initialize config from backend dir"
    # Guard against the specific regression seen in runtime warnings
    assert "No module named 'backend'" not in (proc.stdout + proc.stderr)
    assert "Could not validate LLM compliance levels" not in (proc.stdout + proc.stderr)
    assert "Could not validate MCP compliance levels" not in (proc.stdout + proc.stderr)
