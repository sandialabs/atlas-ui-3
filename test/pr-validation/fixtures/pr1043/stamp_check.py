"""Seed a throwaway database, run the stamp script on it, and check the result."""
import json
import os
import subprocess
import sys

from atlas.modules.chat_history.conversation_repository import ConversationRepository
from atlas.modules.chat_history.database import get_session_factory, init_database, reset_engine

db = sys.argv[1]
url = f"duckdb:///{db}"
init_database(url)
repo = ConversationRepository(get_session_factory())
msg = [{"role": "user", "content": "synthetic"}]
repo.save_conversation("legacy-1", "a@example.com", "t", "m", msg, {"agent_mode": False})
repo.save_conversation("recorded-1", "a@example.com", "t", "m", msg, {"data_classification": "CUI"})
reset_engine()

root = os.environ.get("PYTHONPATH", ".")
script = os.path.join(root, "scripts", "stamp_conversation_classification.py")
env = dict(os.environ, APP_CONFIG_DIR=os.path.dirname(os.path.abspath(__file__)))
out = subprocess.run(
    [sys.executable, script, "--level", "UUR", "--all-users", "--db-url", url],
    capture_output=True, text=True, env=env,
)
print(out.stdout.strip() or out.stderr.strip())

import duckdb  # noqa: E402

rows = dict(duckdb.connect(db).execute("select id, metadata_json from conversations").fetchall())
legacy = json.loads(rows["legacy-1"]).get("data_classification")
recorded = json.loads(rows["recorded-1"]).get("data_classification")
ok = out.returncode == 0 and legacy == "UUR" and recorded == "CUI"
print(f"legacy-1 -> {legacy}, recorded-1 -> {recorded}")
sys.exit(0 if ok else 1)
