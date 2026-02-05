# Easy Start Entrypoint Plan

Last updated: 2026-01-19

This document outlines a proposal to make "git clone → run" dramatically easier for Atlas UI 3 users, while keeping the existing `agent_start.sh` script focused on development use.

## Goals

- Make it trivial for non-developers (or casual users) to run Atlas UI 3 locally.
- Require **either** Docker **or** Python+uv, but **not** Node.js.
- Avoid changing existing developer workflows that rely on `agent_start.sh`.
- Provide a consistent story across Linux/macOS/WSL and Windows.
- Keep configuration and LLM setup explicit, with clear warnings when incomplete.

## High-Level Approach

1. Introduce two new user-facing entrypoint scripts:
   - `easy-start.sh` (Linux/macOS/WSL)
   - `easy-start.ps1` (Windows PowerShell)
2. Keep `agent_start.sh` as a **developer** script only (no behavior change).
3. In docs, make `easy-start.*` the **primary** quickstart path; push Docker-only and manual local setups to secondary sections.
4. Support a **prebuilt frontend** so that most users never need Node.js.
5. Add light checks for `.env` and LLM configuration with good UX (warnings, not surprises).

## User Stories

### 1. New user with Docker only

- Clones the repo.
- Runs:
  ```bash
  bash easy-start.sh
  ```
- Script:
  - Detects Docker, prefers Docker mode.
  - Ensures `.env` exists (copies from `.env.example` if needed).
  - Warns if no LLMs are configured, offers continue/exit.
  - Ensures a frontend build is present (downloads prebuilt bundle for backend to serve).
  - Runs the published image via `docker run` with `--env-file .env`.
  - Prints: "Atlas UI 3 available at http://localhost:8000".

### 2. New user with Python+uv but no Docker / no Node.js

- Has Python 3.12+ and `uv`, but not Docker and not Node.
- Runs:
  ```bash
  bash easy-start.sh
  ```
- Script:
  - Detects Python+uv but not Docker; selects **local Python mode**.
  - Ensures `.env` exists.
  - Performs LLM configuration sanity check.
  - Downloads prebuilt frontend into `frontend/dist`.
  - Creates `.venv` and installs `requirements.txt` via uv.
  - Starts the backend with `python atlas/main.py`.

### 3. Developer with full toolchain (Docker, Python+uv, Node)

- For a quick demo, runs `easy-start.sh`, which defaults to Docker mode (or offers a choice).
- For day-to-day development, follows the Developer Guide and uses:
  ```bash
  uv venv
  source .venv/bin/activate
  uv pip install -r requirements.txt
  bash agent_start.sh
  ```
- `agent_start.sh` remains the dev-only script that builds the frontend locally and starts backend + mocks.

## `easy-start.sh` Responsibilities

`easy-start.sh` should be a small, readable bash script composed of logical steps:

1. **Environment & Mode Detection**
   - Detect Docker: `command -v docker`.
   - Detect Python and uv: `command -v python3` and `command -v uv`.
   - Mode selection:
     - If Docker is available, default to **Docker mode**.
     - If Docker is unavailable but Python+uv is available, use **local Python mode**.
     - If neither is available, exit with a clear error and minimal install hints.
   - Optional: If *both* Docker and Python+uv are available, offer an interactive choice:
     - `[1] Docker (no local dependencies inside the container)`
     - `[2] Local Python (uses uv, suitable for running the code directly)`

2. **`.env` Handling**
   - If `.env` exists, leave it untouched and log `[INFO] Using existing .env`.
   - If `.env` is missing and `.env.example` is present:
     - Copy automatically: `cp .env.example .env`.
     - Log an info message explaining what was done and that the user can edit `.env` later.
   - If `.env.example` is missing, exit with `[ERROR]` and instructions.

3. **LLM Configuration Sanity Check**
   - Simple heuristic:
     - Look for typical provider keys in `.env` (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, etc.).
     - Optionally, check for a known mock LLM configuration/flag if one is supported.
   - Behavior:
     - If at least one provider key or a mock LLM is configured: continue normally.
     - If **no** providers or mocks detected:
       - Print a clear warning block:
         - "[WARN] No LLM configuration detected. The UI will start, but model calls will fail. Set at least one API key in `.env` (e.g., OPENAI_API_KEY)."
       - Prompt the user:
         - `[C]ontinue anyway (for UI-only exploration)`
         - `[E]xit now to edit .env`
       - Respect their choice.

4. **Frontend Availability & Strategy**
   - First, check if `frontend/dist/index.html` already exists:
     - If yes: assume it's usable; skip rebuild/download and continue.
   - If not, fall back to **prebuilt frontend**:
     - Use a stable URL (to be defined) for a packaged build, e.g.:
       - `https://github.com/sandialabs/atlas-ui-3/releases/latest/download/frontend-dist.tar.gz`
     - Download and extract:
       ```bash
       curl -L -o /tmp/frontend-dist.tar.gz "$FRONTEND_DIST_URL"
       mkdir -p frontend
       tar -xzf /tmp/frontend-dist.tar.gz -C frontend
       ```
     - Log: `[INFO] Downloaded prebuilt frontend to frontend/dist (Node.js is not required).`
   - Optional enhancement for dev power-users:
     - If Node/npm is present **and** the user is in local mode, offer a choice:
       - `[1] Download prebuilt frontend (recommended for non-developers)`
       - `[2] Build locally with npm (for developers modifying the frontend)`

5. **Starting the App**

   ### Docker Mode
   - Preferred flow (if a public image is available):
     ```bash
     docker run --rm \
       -p 8000:8000 \
       --env-file .env \
       ghcr.io/sandialabs/atlas-ui-3:latest
     ```
   - Fallback (if relying on local builds):
     - Check if the `atlas-ui-3` image exists; if not, run:
       ```bash
       docker build -t atlas-ui-3 .
       ```
     - Then run:
       ```bash
       docker run --rm -p 8000:8000 --env-file .env atlas-ui-3
       ```

   ### Local Python Mode
   - Ensure uv virtual environment and dependencies:
     ```bash
     uv venv
     source .venv/bin/activate
     uv pip install -r requirements.txt
     ```
   - Start the backend directly (since frontend is already present via prebuilt dist):
     ```bash
     python atlas/main.py
     ```
   - `agent_start.sh` remains a **dev-only** helper that builds the frontend and wires up mocks, and is documented in the Developer Guide.

6. **UX & Non-Interactive Mode**

- Keep terminal output compact and structured with prefixes like `[INFO]`, `[WARN]`, `[ERROR]`.
- Avoid overly chatty prompts; prefer single-line questions with simple single-character responses.
- Provide a `--non-interactive` flag for automation/CI usage:
  - Auto-create `.env` from `.env.example` if missing.
  - Auto-continue past LLM warnings.
  - Auto-download prebuilt frontend when needed.
  - Choose Docker if available, else local Python.

## `easy-start.ps1` Responsibilities

`easy-start.ps1` mirrors `easy-start.sh` but in idiomatic PowerShell for Windows users.

Key differences/considerations:

- Use `Test-Path`, `Get-Command`, and `Copy-Item` instead of bash utilities.
- Typical entrypoint:
  ```powershell
  .\easy-start.ps1
  ```
- Detection logic:
  - Use `Get-Command docker -ErrorAction SilentlyContinue` to detect Docker.
  - Use `Get-Command python` / `py` and `uv` similarly.
- Mode selection and steps (env, LLM check, frontend download, start app) should be conceptually identical to `easy-start.sh`.
- For local Python mode on Windows:
  - Use `uv venv` and activate via `.venv\Scripts\Activate.ps1`.
  - Then run `python atlas/main.py`.

## Documentation Changes

### README

- Add a **Quickstart** section immediately after the project description:

  ```markdown
  ## Quickstart

  Clone the repo and run:

  ```bash
  bash easy-start.sh
  ```

  On Windows (PowerShell):

  ```powershell
  .\easy-start.ps1
  ```

  These scripts will:
  - Ensure `.env` exists (copying from `.env.example` if needed),
  - Check for basic LLM configuration and warn if missing,
  - Download or reuse a frontend build so Node.js is not required for typical use,
  - Start Atlas UI 3 via Docker or local Python, depending on what you have installed.
  ```

- Keep links to the detailed docs (`01_getting_started.md`, `02_admin_guide.md`, `03_developer_guide.md`), but make `easy-start.*` the first recommended path.

### Getting Started (`docs/01_getting_started.md`)

- Add a new top-level section: **Option 1: Easy Start Script (Recommended)**.
- Describe, at a high level, that `easy-start.sh` / `easy-start.ps1`:
  - Detect Docker vs Python.
  - Create `.env` from `.env.example` if missing.
  - Warn if no LLMs are configured.
  - Download a prebuilt frontend if necessary.
  - Start the app on `http://localhost:8000`.
- Move the current Docker and local development instructions under **Option 2: Manual Docker Setup** and **Option 3: Manual Local Development Setup**.

### Developer Guide (`docs/03_developer_guide.md`)

- Explicitly call out that `agent_start.sh` is the preferred entrypoint for active development:
  - Handles frontend builds via npm.
  - Starts the backend and optional MCP mocks.
- Note that `easy-start.*` is aimed at end users and demo operators, not active frontend/backend development.

## Prebuilt Frontend Packaging

To fully realize the "no Node.js required" path, CI should publish a prebaked frontend bundle that `easy-start.*` can download.

- After running `npm install` and `npm run build` in CI, archive `frontend/dist`:
  - Example: `tar -czf frontend-dist.tar.gz -C frontend dist`.
- Attach `frontend-dist.tar.gz` as a release asset or publish it to a stable URL.
- Hardcode or configure this URL in `easy-start.*` as `FRONTEND_DIST_URL`.

This keeps repo size reasonable and avoids committing the built frontend, while still providing a turnkey experience.

## Summary

- Add `easy-start.sh` and `easy-start.ps1` as user-focused, opinionated entrypoints.
- Keep `agent_start.sh` untouched for developers.
- Use the easy-start scripts to:
  - Handle `.env` creation,
  - Warn (and optionally block) when no LLMs are configured,
  - Download a prebuilt frontend so Node.js is optional,
  - Choose Docker vs local Python automatically.
- Update docs so new users see `easy-start.*` as the default path, with manual Docker/dev instructions as advanced options.
