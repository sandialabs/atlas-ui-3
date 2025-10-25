# Frontend Tests

This directory contains unit tests for the React frontend components.

## Running Tests

```bash
cd frontend
npm test
```

## Test Structure

- `Message.test.jsx` - Message component rendering and formatting
- `Header.test.jsx` - Header component and model selection
- `ChatArea.test.jsx` - Chat interface and message input
- `ToolsPanel.test.jsx` - MCP tools selection and management
- `CanvasPanel.test.jsx` - Custom HTML rendering panel
- `Sidebar.test.jsx` - Navigation sidebar component
- `WelcomeScreen.test.jsx` - Welcome screen for new users
- `RagPanel.test.jsx` - RAG data source selection
- `BannerPanel.test.jsx` - System banner display
- `MarketplacePanel.test.jsx` - MCP server marketplace

## Testing Framework

- **Vitest**: Fast unit testing framework
- **@testing-library/react**: React component testing utilities
- **@testing-library/jest-dom**: Additional Jest matchers
- **@testing-library/user-event**: User interaction simulation

## Test Coverage

These are basic unit tests focused on:
- Component rendering
- User interactions
- Context integration
- Props handling
- Error states

## Running Specific Tests

```bash
# Run specific test file
npm test Message.test.jsx

# Run tests in watch mode
npm test -- --watch

# Run tests with UI
npm run test:ui
```