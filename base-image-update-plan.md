# Base Image Update Plan: Ubuntu → Fedora

## Overview
Migrate from Ubuntu 24.04 to Fedora:latest base image and ensure all tests pass. Remove Playwright dependency issues while maintaining comprehensive testing coverage.

## Current State Analysis
- **Base Images**: Ubuntu 24.04 in both `Dockerfile` and `Dockerfile-test`
- **Testing Strategy**: Mix of Playwright (problematic) and simple E2E tests with Beautiful Soup
- **CI/CD**: GitHub Actions using test container → build production → push
- **Current Tests**: Backend tests, frontend tests, E2E tests (Playwright + simple Python)

## Migration Plan

### Phase 1: Update Base Images
1. **Replace Ubuntu with Fedora:latest** in both Dockerfiles
2. **Update package managers**: `apt-get` → `dnf`
3. **Update package names**: Fedora equivalents for system dependencies
4. **Fix Node.js installation**: Use Fedora's Node.js packages or NodeSource for Fedora

### Phase 2: Comment Out Playwright Dependencies
1. **Comment out Playwright tests** in test scripts (DO NOT DELETE)
2. **Keep only Beautiful Soup-based E2E tests** (`simple_e2e_test.py`)
3. **Update test runners** to skip Playwright
4. **Comment out Playwright dependencies** in package.json (keep for future)

### Phase 3: Fedora-Specific Adjustments
1. **User management**: Fedora uses different commands for user creation
2. **Python setup**: Ensure Python 3.12 is available on Fedora
3. **uv installer**: Verify uv works on Fedora
4. **System dependencies**: Update curl, hostname, sudo installation

### Phase 4: Testing Strategy
1. **Keep simple E2E tests**: HTTP requests to test API endpoints
2. **Keep backend tests**: pytest-based unit tests
3. **Keep frontend tests**: Vitest/Jest tests (no browser required)
4. **Comment out**: All Playwright browser-based tests

### Phase 5: Local Testing & CI/CD
1. **Test locally** with new Dockerfiles
2. **Fix any Fedora-specific issues**
3. **Commit and push** to trigger GitHub Actions
4. **Monitor CI/CD** and fix failures iteratively

## Key Changes

### Package Manager Changes
- `apt-get update && apt-get install -y` → `dnf update -y && dnf install -y`
- `apt-get clean && rm -rf /var/lib/apt/lists/*` → `dnf clean all`

### System Package Mapping
- `python3` → `python3` (same)
- `python3-pip` → `python3-pip` (same)
- `python3-venv` → `python3-virtualenv`
- `nodejs` → `nodejs`
- `npm` → `npm`
- `curl` → `curl` (same)
- `hostname` → `hostname` (same)
- `sudo` → `sudo` (same)
- `ca-certificates` → `ca-certificates` (same)
- `dos2unix` → `dos2unix` (same)
- `wget` → `wget` (same)

### Node.js Installation
- Replace NodeSource Ubuntu repo with Fedora approach
- Use either Fedora's built-in Node.js or NodeSource Fedora repo

### User Management
- `groupadd -r appuser && useradd -r -g appuser appuser` should work the same on Fedora

### Testing Changes
- Comment out Playwright test execution in `test/e2e_tests.sh`
- Keep `simple_e2e_test.py` as primary E2E testing
- Comment out Playwright dependencies in `frontend/package.json`
- Update test scripts to skip Playwright steps

## Risk Mitigation
- **Incremental approach**: Test each Dockerfile separately
- **Fallback plan**: Can revert to Ubuntu if Fedora causes major issues
- **Simple tests**: Focus on HTTP-based tests that don't depend on browser automation
- **Preserve Playwright**: Comment out rather than delete for future use

## Success Criteria
1. Both Dockerfiles build successfully with Fedora base
2. All non-Playwright tests pass locally
3. CI/CD pipeline passes with new configuration
4. Application runs correctly in Fedora container
5. API endpoints are accessible and functional

## Files to Modify
- `Dockerfile` - Production image
- `Dockerfile-test` - Test image
- `test/e2e_tests.sh` - Comment out Playwright execution
- `frontend/package.json` - Comment out Playwright dependencies
- Any other test scripts that reference Playwright

## Timeline
- Phase 1-3: Update Dockerfiles and dependencies
- Phase 4: Update test configuration
- Phase 5: Local testing and CI/CD validation

## Notes
- Always use timeouts for network operations
- Test locally before pushing to CI/CD
- Monitor resource usage during Fedora migration
- Keep detailed logs of any issues encountered