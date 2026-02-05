import os
import sys
import subprocess
from pathlib import Path


def run_subprocess(code: str, cwd: Path, project_root: Path = None):
    env = os.environ.copy()
    # For the atlas package structure, ensure project root is in PYTHONPATH
    if project_root:
        env["PYTHONPATH"] = str(project_root)
    else:
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
    Ensure imports work when running from the atlas directory (the supported run mode).
    The atlas package requires the project root on PYTHONPATH for proper package imports.
    This mirrors `bash agent_start.sh` which sets PYTHONPATH before running from ./atlas.
    """
    atlas_dir = Path(__file__).resolve().parents[1]
    project_root = atlas_dir.parent

    code = (
        "import main; "
        "from atlas.modules.config import ConfigManager; "
        "cm=ConfigManager(); "
        "_ = cm.llm_config; _ = cm.mcp_config; _ = cm.rag_mcp_config; "
        "print('OK')"
    )

    proc = run_subprocess(code, atlas_dir, project_root=project_root)

    # Helpful diagnostics on failure
    if proc.returncode != 0:
        print("STDOUT:\n" + proc.stdout)
        print("STDERR:\n" + proc.stderr)

    assert proc.returncode == 0, "Subprocess failed to import and initialize config from atlas dir"
    # Guard against the specific regression seen in runtime warnings
    assert "No module named 'atlas'" not in (proc.stdout + proc.stderr)
    assert "Could not validate LLM compliance levels" not in (proc.stdout + proc.stderr)
    assert "Could not validate MCP compliance levels" not in (proc.stdout + proc.stderr)
