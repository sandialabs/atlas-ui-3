# Load and scale profiles

Reproducible load profiles for multi-replica Atlas (issue #1129), built on the
multi-replica harness in `../multireplica`. No real LLM.

```bash
bash test/load_tests.sh --list
bash test/load_tests.sh smoke
bash test/load_tests.sh reconnect-storm --scale 2 --out /tmp/storm
```

- `atlas_load/`: runner (`python -m atlas_load`), virtual users, metrics,
  `/proc` + Redis + Postgres sampling, report, mock IdP and mock LLM wrapper
- `profiles/`: bundled profiles (JSON)
- `test_load_tooling.py`: unit tests, no infrastructure
- `test_load_smoke.py`: runs the `smoke` profile end to end (needs podman)

Full guide, profile format and how to read the report:
[docs/developer/load-testing.md](../../docs/developer/load-testing.md).
