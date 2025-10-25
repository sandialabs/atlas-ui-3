# End-to-End Tests for Chat UI MCP Marketplace

This folder contains comprehensive end-to-end tests that reproduce the manual testing workflow for the MCP (Model Context Protocol) Marketplace feature.

## What These Tests Do

The tests reproduce the exact workflow that was performed manually:

1. **Environment Setup**: Ensures the application is running and browsers are installed
2. **Page Navigation**: Loads the Chat UI homepage and verifies core elements
3. **Tools Panel**: Opens the Tools & Integrations panel 
4. **Marketplace Navigation**: Navigates to the MCP Marketplace
5. **MCP Server Verification**: Validates that all MCP server cards display enhanced metadata fields

## Prerequisites

Before running the tests, ensure:

1. **Backend Server Running**: The uvicorn server must be running on port 8000
   ```bash
   cd /workspaces/atlas-ui-3-11/backend
   python -c "
   import uvicorn
   from main import app
   from config import config_manager
   uvicorn.run(app, host='0.0.0.0', port=config_manager.app_settings.port, reload=False)
   " &
   ```

2. **Frontend Built**: The frontend must be built for production
   ```bash
   cd /workspaces/atlas-ui-3-11/frontend
   npm run build
   ```

## Installation

1. Install test dependencies:
   ```bash
   cd /workspaces/atlas-ui-3-11/test_e2e
   npm install
   ```

2. Install Playwright browsers:
   ```bash
   npx playwright install
   ```

## Running the Tests

### Run all tests
```bash
npm test
```

### Run tests with UI (interactive mode)
```bash
npm run test:ui
```

### Run tests in headed mode (see browser)
```bash
npm run test:headed
```

### Run specific test
```bash
npx playwright test marketplace.spec.js
```

## Test Coverage

The test suite includes:

### 🏠 **Homepage Tests**
- ✅ Page loads correctly
- ✅ Chat UI title and branding display
- ✅ Welcome screen shows proper messaging

### 🔧 **Tools Panel Tests**
- ✅ Toggle Tools button works
- ✅ Tools & Integrations panel becomes visible
- ✅ Marketplace navigation buttons appear

### 🛒 **Marketplace Tests**
- ✅ Marketplace page loads at correct URL
- ✅ Page title and description display
- ✅ Server selection counter works
- ✅ Control buttons (Select All, Deselect All, Back to Chat) present

### 📋 **MCP Server Card Tests**
- ✅ All 7 MCP servers display correctly
- ✅ **Enhanced metadata fields verified:**
  - Short descriptions appear in blue text
  - Server names display as headings
  - Tool counts show correctly
- ✅ Individual server cards:
  - Calculator: "Basic mathematical calculations"
  - Thinking: "Structured thinking and problem analysis tool"
  - PDF Basic: "PDF analysis tool"
  - UI Demo: "Demo server showcasing custom UI modification capabilities"
  - Code Executor: "Secure code execution environment"
  - Prompts: "Specialized system prompts for AI behavior modification"
  - Canvas: "Canvas for showing final rendered content..."

### 🎯 **Interactive Tests**
- ✅ Server selection/deselection works
- ✅ Selection counter updates correctly
- ✅ Visual feedback for selected servers

### 📖 **Help Content Tests**
- ✅ "How it works" section displays
- ✅ Purple/gray tag explanations present
- ✅ Usage instructions clear

## Test Output

Tests generate:
- **HTML Report**: Detailed test results with screenshots
- **Trace Files**: For debugging failed tests
- **Console Logs**: Application debug information during tests

## Troubleshooting

### Server Not Running
```
Error: connect ECONNREFUSED ::1:8000
```
**Solution**: Start the uvicorn server first (see Prerequisites)

### Frontend Not Built
```
404 errors or stale content
```
**Solution**: Run `npm run build` in the frontend directory

### Browser Installation Issues
```
Browser not found errors
```
**Solution**: Run `npx playwright install`

## Integration with CI/CD

These tests can be integrated into your CI/CD pipeline by:

1. Starting the server in the background
2. Building the frontend
3. Running the test suite
4. Collecting test artifacts

Example GitHub Actions step:
```yaml
- name: Run E2E Tests
  run: |
    cd test_e2e
    npm install
    npx playwright install
    npm test
```

## Extending the Tests

To add new test cases:

1. Add test functions to `tests/marketplace.spec.js`
2. Follow the existing pattern of page navigation and assertions
3. Use descriptive test names and organize by feature area
4. Include proper error handling and timeouts

## Notes

- Tests use a 1400x1000 viewport for consistency
- WebSocket connection delays are handled with appropriate waits
- Tests are designed to be independent and can run in parallel
- Browser state is reset between test runs