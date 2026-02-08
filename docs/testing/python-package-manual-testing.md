# Python Package Manual Testing Checklist

Last updated: 2026-02-04

This checklist guides you through manually testing the Python package features added in PR #275.

## Prerequisites

- [ ] You have a working Python 3.11+ environment
- [ ] You have at least one LLM API key (OpenAI, Anthropic, or Google)
- [ ] You are in the `atlas-ui-3` repository root directory

---

## Part 1: Editable Install

### 1.1 Install in Editable Mode

```bash
# Create fresh virtual environment
uv venv --python 3.11
source .venv/bin/activate

# Install in editable mode
uv pip install -e .
```

- [ ] Installation completes without errors
- [ ] No import errors or missing dependencies

### 1.2 Verify Editable Mode Works

```bash
# Check that atlas points to local source
python -c "import atlas; print(atlas.__file__)"
```

- [ ] Output shows path to your local `atlas/__init__.py` (not site-packages)

### 1.3 Verify CLI Commands Are Installed

```bash
which atlas-init
which atlas-chat
which atlas-server
```

- [ ] All three commands are found in `.venv/bin/`

---

## Part 2: atlas-init Command

### 2.1 Test atlas-init --help

```bash
atlas-init --help
```

- [ ] Help text displays correctly
- [ ] Shows --target, --minimal, --force options

### 2.2 Test Full Setup (New Directory)

```bash
# Create test directory
mkdir -p /tmp/atlas-test-full
atlas-init --target /tmp/atlas-test-full --force

# Check results
ls -la /tmp/atlas-test-full/
ls -la /tmp/atlas-test-full/config/
```

- [ ] `.env` file created
- [ ] `config/` directory created
- [ ] `config/llmconfig.yml` exists
- [ ] `config/mcp.json` exists
- [ ] Next steps message displayed

### 2.3 Test Minimal Setup

```bash
mkdir -p /tmp/atlas-test-minimal
atlas-init --target /tmp/atlas-test-minimal --minimal --force

cat /tmp/atlas-test-minimal/.env
```

- [ ] Only `.env` file created (no config folder)
- [ ] `.env` contains API key placeholders
- [ ] `.env` contains PORT and DEBUG_MODE settings

### 2.4 Test Overwrite Prompting

```bash
# First setup
mkdir -p /tmp/atlas-test-prompt
atlas-init --target /tmp/atlas-test-prompt --force

# Second setup without --force (should prompt)
atlas-init --target /tmp/atlas-test-prompt
# Answer 'n' to skip overwriting
```

- [ ] Prompts "already exists. Overwrite?" for each file
- [ ] Answering 'n' skips the file
- [ ] Answering 'y' overwrites the file

### 2.5 Test Version Flag

```bash
atlas-init --version
```

- [ ] Shows version number (e.g., "atlas-init version 0.1.0")

---

## Part 3: atlas-chat Command

### 3.1 Setup for Testing

```bash
# Set up a test environment
mkdir -p /tmp/atlas-chat-test
cd /tmp/atlas-chat-test
atlas-init --minimal --force

# Edit .env to add your real API key
# nano .env  # or use your preferred editor
```

- [ ] Created test directory with .env

### 3.2 Test atlas-chat --help

```bash
atlas-chat --help
```

- [ ] Help text displays correctly
- [ ] Shows options for --model, --tools, --agent, etc.

### 3.3 Test --list-models

```bash
atlas-chat --list-models
```

- [ ] Lists available models from configuration
- [ ] No errors or exceptions

### 3.4 Test --list-tools

```bash
atlas-chat --list-tools
```

- [ ] Lists available tools (may be empty if no MCP servers configured)
- [ ] No errors or exceptions

### 3.5 Test Simple Chat (requires API key)

```bash
# Make sure OPENAI_API_KEY or another key is set
export OPENAI_API_KEY="sk-your-key"

atlas-chat "What is 2+2? Reply with just the number."
```

- [ ] Returns a response from the LLM
- [ ] Response contains "4"

### 3.6 Test Streaming Output

```bash
atlas-chat "Count from 1 to 5 slowly" --stream
```

- [ ] Tokens appear incrementally (not all at once)

### 3.7 Test Specific Model

```bash
atlas-chat "Say hello" --model gpt-4o
```

- [ ] Uses the specified model
- [ ] Returns a response

---

## Part 4: atlas-server Command

### 4.1 Test atlas-server --help

```bash
atlas-server --help
```

- [ ] Help text displays correctly
- [ ] Shows --port, --host, --env, --config-folder options

### 4.2 Test Version Flag

```bash
atlas-server --version
```

- [ ] Shows version number

### 4.3 Test Server Startup

```bash
# In test directory with .env
cd /tmp/atlas-chat-test

# Start server (will run in foreground)
atlas-server --port 8001 &
SERVER_PID=$!

# Wait for startup
sleep 3

# Test health endpoint
curl http://localhost:8001/api/config

# Stop server
kill $SERVER_PID
```

- [ ] Server starts without errors
- [ ] Shows "Starting Atlas server on 127.0.0.1:8001"
- [ ] `/api/config` returns JSON response
- [ ] Server stops cleanly

### 4.4 Test Custom Port and Host

```bash
atlas-server --port 9000 --host 0.0.0.0 &
SERVER_PID=$!
sleep 3

curl http://localhost:9000/api/config

kill $SERVER_PID
```

- [ ] Server binds to specified port and host

### 4.5 Test Custom Env File

```bash
# Create custom env file
echo "PORT=8888" > /tmp/custom.env
echo "DEBUG_MODE=true" >> /tmp/custom.env

atlas-server --env /tmp/custom.env &
SERVER_PID=$!
sleep 3

curl http://localhost:8888/api/config

kill $SERVER_PID
```

- [ ] Server uses settings from custom env file

---

## Part 5: Python API

### 5.1 Test Import

```bash
python -c "from atlas import AtlasClient, ChatResult; print('Import OK')"
```

- [ ] Import succeeds without errors

### 5.2 Test Lazy Loading

```bash
# This should be fast (no heavy imports until AtlasClient is used)
time python -c "import atlas"
```

- [ ] Import completes quickly (< 1 second)

### 5.3 Test Synchronous Chat

```bash
export OPENAI_API_KEY="sk-your-key"

python << 'EOF'
from atlas import AtlasClient

client = AtlasClient()
result = client.chat_sync("What is the capital of France? One word answer.")
print(f"Response: {result.message}")
print(f"Session ID: {result.session_id}")
EOF
```

- [ ] Returns response mentioning "Paris"
- [ ] Session ID is a valid UUID

### 5.4 Test Async Chat

```bash
python << 'EOF'
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()
    result = await client.chat("Say 'hello world'")
    print(f"Response: {result.message}")
    await client.cleanup()

asyncio.run(main())
EOF
```

- [ ] Returns response with "hello world"
- [ ] Cleanup completes without errors

### 5.5 Test ChatResult Structure

```bash
python << 'EOF'
from atlas import AtlasClient

client = AtlasClient()
result = client.chat_sync("Hi")

print(f"message: {type(result.message)}")
print(f"tool_calls: {type(result.tool_calls)}")
print(f"files: {type(result.files)}")
print(f"canvas_content: {type(result.canvas_content)}")
print(f"session_id: {type(result.session_id)}")

# Test to_dict
d = result.to_dict()
print(f"to_dict keys: {list(d.keys())}")
EOF
```

- [ ] message is str
- [ ] tool_calls is list
- [ ] files is dict
- [ ] canvas_content is str or NoneType
- [ ] session_id is UUID
- [ ] to_dict returns dict with expected keys

### 5.6 Test Multi-turn Conversation

```bash
python << 'EOF'
import asyncio
from atlas import AtlasClient

async def main():
    client = AtlasClient()

    # First message
    r1 = await client.chat("My name is Alice. Remember it.")
    session_id = r1.session_id
    print(f"First response: {r1.message[:100]}...")

    # Second message in same session
    r2 = await client.chat("What is my name?", session_id=session_id)
    print(f"Second response: {r2.message}")

    await client.cleanup()

asyncio.run(main())
EOF
```

- [ ] Second response mentions "Alice"
- [ ] Same session_id maintained across calls

---

## Part 6: agent_start.sh (Development Server)

### 6.1 Test Full Startup

```bash
cd /path/to/atlas-ui-3
bash agent_start.sh
```

- [ ] Frontend builds successfully
- [ ] Backend starts on port 8000
- [ ] No import errors in logs
- [ ] Web UI accessible at http://localhost:8000

### 6.2 Test Backend-Only Mode

```bash
bash agent_start.sh -b
```

- [ ] Skips frontend build
- [ ] Backend starts successfully

### 6.3 Test Frontend-Only Mode

```bash
bash agent_start.sh -f
```

- [ ] Only rebuilds frontend
- [ ] Exits after build completes

---

## Part 7: PowerShell Script (Windows)

*Skip if not on Windows*

### 7.1 Test Full Startup

```powershell
.\ps_agent_start.ps1
```

- [ ] Frontend builds successfully
- [ ] Backend starts on port 8000
- [ ] PYTHONPATH is set correctly

---

## Part 8: Integration Tests

### 8.1 Run Backend Tests

```bash
cd /path/to/atlas-ui-3
.venv/bin/python -m pytest atlas/tests/ -v --tb=short
```

- [ ] All tests pass (750 expected)
- [ ] No import errors

### 8.2 Test Runtime Imports

```bash
.venv/bin/python -m pytest atlas/tests/test_runtime_imports.py -v
```

- [ ] test_backend_dir_imports_work_without_project_root_in_path passes

---

## Summary Checklist

| Category | Tests | Passed |
|----------|-------|--------|
| Editable Install | 3 | [ ] |
| atlas-init | 5 | [ ] |
| atlas-chat | 7 | [ ] |
| atlas-server | 5 | [ ] |
| Python API | 6 | [ ] |
| agent_start.sh | 3 | [ ] |
| PowerShell (Windows) | 1 | [ ] |
| Integration Tests | 2 | [ ] |

**Total: 32 tests**

---

## Troubleshooting

### "No module named 'atlas'"

Make sure PYTHONPATH includes the project root:
```bash
export PYTHONPATH=/path/to/atlas-ui-3
```

Or reinstall in editable mode:
```bash
uv pip install -e .
```

### API Key Errors

Make sure your API key is set:
```bash
export OPENAI_API_KEY="sk-..."
```

Or add it to your `.env` file.

### Port Already in Use

Kill existing processes:
```bash
pkill -f "uvicorn main:app"
```

Or use a different port:
```bash
atlas-server --port 8001
```

---

## Part 9: PyPI Integration Setup

This section covers how to set up automated publishing to PyPI.

### 9.1 Prerequisites

- [ ] PyPI account at https://pypi.org
- [ ] GitHub repository with Actions enabled
- [ ] Admin access to add repository secrets

### 9.2 Create PyPI API Token

1. Log in to https://pypi.org
2. Go to Account Settings → API tokens
3. Click "Add API token"
4. Name: `atlas-chat-github-actions`
5. Scope: Select "Entire account" (or scope to `atlas-chat` project after first publish)
6. Copy the token (starts with `pypi-`)

- [ ] Token created and copied

### 9.3 Add GitHub Secret

1. Go to your GitHub repository → Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: `PYPI_API_TOKEN`
4. Value: Paste the PyPI token
5. Click "Add secret"

- [ ] Secret `PYPI_API_TOKEN` added to repository

### 9.4 Create GitHub Actions Workflow

Create `.github/workflows/publish-pypi.yml`:

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to publish (must match pyproject.toml)'
        required: true

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # For trusted publishing (optional)
      contents: read

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install build dependencies
        run: |
          python -m pip install --upgrade pip
          pip install build twine

      - name: Build package
        run: python -m build

      - name: Check package
        run: twine check dist/*

      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: twine upload dist/*
```

- [ ] Workflow file created at `.github/workflows/publish-pypi.yml`

### 9.5 Test Local Build

Before publishing, verify the package builds correctly:

```bash
# Install build tools
uv pip install build twine

# Build the package
python -m build

# Check the built package
twine check dist/*

# List contents
ls -la dist/
```

- [ ] Package builds without errors
- [ ] `dist/atlas_chat-0.1.0.tar.gz` created
- [ ] `dist/atlas_chat-0.1.0-py3-none-any.whl` created
- [ ] `twine check` passes

### 9.6 Test Package Installation from Built Files

```bash
# Create a fresh virtual environment
python -m venv /tmp/test-atlas-install
source /tmp/test-atlas-install/bin/activate

# Install from the wheel
pip install dist/atlas_chat-0.1.0-py3-none-any.whl

# Verify installation
atlas-init --version
atlas-chat --help
atlas-server --version

# Test import
python -c "from atlas import AtlasClient; print('Import OK')"

# Cleanup
deactivate
rm -rf /tmp/test-atlas-install
```

- [ ] Package installs from wheel
- [ ] CLI commands work
- [ ] Python import works

### 9.7 Publish to TestPyPI (Optional but Recommended)

Before publishing to the real PyPI, test with TestPyPI:

1. Create an account at https://test.pypi.org
2. Create an API token
3. Add `TEST_PYPI_API_TOKEN` secret to GitHub

```bash
# Manual upload to TestPyPI
twine upload --repository testpypi dist/*

# Test installation from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ atlas-chat
```

- [ ] Published to TestPyPI successfully
- [ ] Installation from TestPyPI works

### 9.8 Trigger a Release

To publish to PyPI:

**Option A: GitHub Release (Recommended)**
1. Go to Releases → "Create a new release"
2. Tag: `v0.1.0` (must match version in `pyproject.toml`)
3. Title: `v0.1.0 - Initial Python Package Release`
4. Description: Release notes
5. Click "Publish release"

**Option B: Manual Workflow Dispatch**
1. Go to Actions → "Publish to PyPI"
2. Click "Run workflow"
3. Enter version number
4. Click "Run workflow"

- [ ] Release triggered
- [ ] GitHub Action completes successfully
- [ ] Package visible at https://pypi.org/project/atlas-chat/

### 9.9 Verify Published Package

```bash
# Create fresh environment
python -m venv /tmp/test-pypi-install
source /tmp/test-pypi-install/bin/activate

# Install from PyPI
pip install atlas-chat

# Verify
atlas-init --version
atlas-chat --help
python -c "from atlas import AtlasClient; print('Import OK')"

# Cleanup
deactivate
rm -rf /tmp/test-pypi-install
```

- [ ] Package installs from PyPI
- [ ] All CLI commands work
- [ ] Python import works

---

## PyPI Integration Summary

| Step | Status |
|------|--------|
| PyPI API Token | [ ] |
| GitHub Secret | [ ] |
| Workflow File | [ ] |
| Local Build | [ ] |
| TestPyPI (optional) | [ ] |
| Production Release | [ ] |
| Verification | [ ] |

### Version Bumping Checklist

When releasing a new version:

1. Update version in `pyproject.toml`:
   ```toml
   version = "0.2.0"
   ```

2. Update CHANGELOG.md with release notes

3. Commit changes:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "Bump version to 0.2.0"
   git push
   ```

4. Create GitHub release with tag `v0.2.0`

5. Verify the new version appears on PyPI
