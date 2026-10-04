# Test Directory

This directory contains the centralized testing infrastructure for the project.

## Structure

```
test/
├── README.md           # This file
├── run_tests.sh        # Master test script (entry point)
├── atlas_tests.sh      # Backend (atlas) test execution
├── frontend_tests.sh   # Frontend test execution
└── e2e_tests.sh        # End-to-end test execution
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
./test/atlas_tests.sh
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

The CI/CD pipeline (`.github/workflows/ci.yml`) builds and tests in parallel:
1. The test job builds the test image, runs all suites with debug mode enabled,
   then runs the backend suite in production mode.
2. The production and runtime-only images are validated in separate jobs.
3. On branch pushes a separate publish job pushes the multi-platform production
   image only after the test and runtime-only jobs pass.
4. The non-blocking reverse-order backend run and the production-mode e2e run
   happen on pushes to `main`, not on pull requests.

The pull-request status checks are `test`, `production-image`, and
`runtime-only-image`.

## Local Testing

To test the containerized approach locally:

```bash
# From project root
./test_container_locally.sh
```

## Test Status

Currently configured to run only working tests:
- Backend: 17 passing tests
- Frontend: 3 passing tests  
- E2E: Disabled (no working tests)

See `TEST_STATUS.md` in the project root for details on disabled tests and re-enabling strategy.