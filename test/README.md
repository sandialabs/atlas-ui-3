# Test Directory

This directory contains the centralized testing infrastructure for the project.

## Structure

```
test/
├── README.md              # This file
├── run_tests.sh           # Master test script (entry point)
├── backend_tests.sh       # Backend test execution
├── frontend_tests.sh      # Frontend test execution
├── e2e_tests.sh           # End-to-end test execution
├── simple_e2e_test.py     # Simple Python-based e2e tests
├── oauth_e2e_test.py      # OAuth 2.1 authentication e2e tests
└── OAUTH_E2E_TESTS.md     # OAuth 2.1 test documentation
```

## Usage

### Master Test Script
The main entry point for all testing:

```bash
# Run all tests
./test/run_tests.sh all

# Run specific test suites
./test/run_tests.sh backend
./test/run_tests.sh frontend
./test/run_tests.sh e2e
```

### Individual Test Scripts
Each test type has its own script that can be run independently:

```bash
./test/backend_tests.sh
./test/frontend_tests.sh
./test/e2e_tests.sh
```

## Container Integration

The test scripts are designed to run inside Docker containers with the following assumptions:
- Application code is mounted at `/app`
- Python dependencies are pre-installed
- Node.js dependencies are pre-installed
- Working directory is `/app`

## CI/CD Integration

The CI/CD pipeline (`.github/workflows/ci.yml`) uses this approach:
1. Build Docker image with all dependencies
2. Run `bash /app/test/run_tests.sh all` inside the container
3. Push image only if all tests pass

## Local Testing

To test the containerized approach locally:

```bash
# From project root
./test_container_locally.sh
```

## Test Status

Currently configured test coverage:
- Backend: 17 passing tests
- Frontend: 3 passing tests  
- E2E: Simple e2e tests + OAuth 2.1 authentication tests

### E2E Tests

The e2e test suite includes:

1. **Simple E2E Tests** (`simple_e2e_test.py`): Basic functionality tests using requests library
2. **OAuth 2.1 Tests** (`oauth_e2e_test.py`): Bearer token authentication flow tests
3. **Playwright Tests** (`../test_e2e/tests/*.spec.js`): Browser-based UI tests

For detailed information on OAuth 2.1 tests, see `OAUTH_E2E_TESTS.md`.

See `TEST_STATUS.md` in the project root for details on disabled tests and re-enabling strategy.