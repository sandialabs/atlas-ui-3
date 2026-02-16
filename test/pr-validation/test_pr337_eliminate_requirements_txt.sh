#!/bin/bash
# PR #337 Validation Script: Remove requirements.txt, consolidate deps into pyproject.toml
# Tests that requirements.txt is removed, editable install works, imports work,
# no stale references remain, and all tests pass.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #337 Validation: Eliminate requirements.txt"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

echo ""
echo "1. Verify requirements.txt is removed"
echo "--------------------------------------"
if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
    echo "FAILED: requirements.txt still exists in project root"
    exit 1
else
    echo "PASSED: requirements.txt removed from project root"
fi

echo ""
echo "2. Verify pyproject.toml has all dependencies"
echo "----------------------------------------------"
# Check key dependencies that were in requirements.txt
for dep in fastapi litellm fastmcp httpx boto3 pydantic uvicorn websockets; do
    if grep -q "\"$dep" "$PROJECT_ROOT/pyproject.toml"; then
        echo "  PASSED: $dep found in pyproject.toml"
    else
        echo "  FAILED: $dep missing from pyproject.toml"
        exit 1
    fi
done
echo "PASSED: All key dependencies present in pyproject.toml"

echo ""
echo "3. Verify fastmcp has upper bound constraint"
echo "---------------------------------------------"
if grep -q 'fastmcp>=2.10.0,<3.0.0' "$PROJECT_ROOT/pyproject.toml"; then
    echo "PASSED: fastmcp has <3.0.0 upper bound"
else
    echo "FAILED: fastmcp missing <3.0.0 upper bound"
    exit 1
fi

echo ""
echo "4. Verify editable install works"
echo "---------------------------------"
pip show atlas-chat > /dev/null 2>&1 && echo "PASSED: atlas-chat package is installed" || {
    echo "INFO: Package not installed, attempting editable install..."
    uv pip install -e ".[dev]" && echo "PASSED: Editable install succeeded" || {
        echo "FAILED: Editable install failed"
        exit 1
    }
}

echo ""
echo "5. Verify imports work without PYTHONPATH"
echo "------------------------------------------"
(unset PYTHONPATH && python -c "from atlas import AtlasClient, ChatResult; print('OK')") && echo "PASSED: Imports work without PYTHONPATH" || {
    echo "FAILED: Imports require PYTHONPATH (editable install broken)"
    exit 1
}

echo ""
echo "6. Verify CLI entry points work"
echo "--------------------------------"
cd "$PROJECT_ROOT/atlas"
python atlas_chat_cli.py --help > /dev/null 2>&1 && echo "PASSED: atlas_chat_cli.py --help works" || {
    echo "FAILED: atlas_chat_cli.py --help failed"
    exit 1
}
python server_cli.py --help > /dev/null 2>&1 && echo "PASSED: server_cli.py --help works" || {
    echo "FAILED: server_cli.py --help failed"
    exit 1
}
cd "$PROJECT_ROOT"

echo ""
echo "7. Verify no stale requirements.txt references in scripts"
echo "----------------------------------------------------------"
# Check main scripts (exclude mocks/, docs/, CHANGELOG, AI instruction files)
STALE_REFS=$(grep -rn "requirements\.txt" \
    agent_start.sh \
    test/atlas_tests.sh \
    test/e2e_tests.sh \
    test/e2e_tests_live.sh \
    Dockerfile \
    Dockerfile-test \
    .github/workflows/ \
    2>/dev/null | grep -v "^Binary" || true)

if [ -n "$STALE_REFS" ]; then
    echo "FAILED: Stale requirements.txt references found:"
    echo "$STALE_REFS"
    exit 1
else
    echo "PASSED: No stale requirements.txt references in scripts/Dockerfiles/CI"
fi

echo ""
echo "8. Verify no unnecessary PYTHONPATH exports in test scripts"
echo "------------------------------------------------------------"
PYTHONPATH_REFS=$(grep -n "export PYTHONPATH" \
    test/atlas_tests.sh \
    test/e2e_tests.sh \
    test/e2e_tests_live.sh \
    2>/dev/null || true)

if [ -n "$PYTHONPATH_REFS" ]; then
    echo "FAILED: Stale PYTHONPATH exports found in test scripts:"
    echo "$PYTHONPATH_REFS"
    exit 1
else
    echo "PASSED: No unnecessary PYTHONPATH exports in test scripts"
fi

echo ""
echo "9. Verify Dockerfile layer caching structure"
echo "----------------------------------------------"
# Check that Dockerfile installs deps before copying source
if grep -q "uv pip install \." "$PROJECT_ROOT/Dockerfile" && \
   grep -q "COPY --chown=appuser:appuser atlas/" "$PROJECT_ROOT/Dockerfile" && \
   grep -q "no-deps" "$PROJECT_ROOT/Dockerfile"; then
    echo "PASSED: Dockerfile uses two-step install for layer caching"
else
    echo "WARNING: Dockerfile may not have optimal layer caching"
fi

echo ""
echo "10. Verify no duplicate COPY pyproject.toml in Dockerfile-test"
echo "---------------------------------------------------------------"
PYPROJECT_COPIES=$(grep -c "COPY pyproject.toml" "$PROJECT_ROOT/Dockerfile-test")
if [ "$PYPROJECT_COPIES" -le 1 ]; then
    echo "PASSED: No duplicate COPY pyproject.toml in Dockerfile-test"
else
    echo "FAILED: Found $PYPROJECT_COPIES COPY pyproject.toml lines in Dockerfile-test"
    exit 1
fi

echo ""
echo "11. Verify S3 client eager instantiation is removed"
echo "----------------------------------------------------"
if python -c "
import ast, sys
with open('$PROJECT_ROOT/atlas/modules/file_storage/__init__.py') as f:
    tree = ast.parse(f.read())
# Check for module-level assignments that call S3StorageClient()
for node in ast.iter_child_nodes(tree):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in ('s3_client', 'file_manager'):
                print(f'Found eager instantiation: {target.id}')
                sys.exit(1)
print('No eager S3 instantiation found')
"; then
    echo "PASSED: No eager S3 client instantiation at import time"
else
    echo "FAILED: Eager S3 client instantiation still present"
    exit 1
fi

echo ""
echo "12. Run backend unit tests"
echo "---------------------------"
cd "$PROJECT_ROOT"
./test/run_tests.sh backend && echo "PASSED: Backend tests pass" || {
    echo "FAILED: Backend tests failed"
    exit 1
}

echo ""
echo "=========================================="
echo "PR #337 Validation: ALL CHECKS PASSED"
echo "=========================================="
exit 0
