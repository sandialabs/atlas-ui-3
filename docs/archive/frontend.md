# Frontend Development Guide

The frontend is a modern React application built with Vite, providing a responsive chat interface with MCP integration.

## Technology Stack

- **React 19** with functional components and hooks
- **Vite** for fast development and building
- **Tailwind CSS** for styling
- **Lucide React** for icons
- **WebSocket** for real-time communication

## Architecture Overview

### Component Structure

```
src/
├── components/          # React components
│   ├── AgentModal.jsx   # Agent mode interface
│   ├── BannerPanel.jsx  # System banners
│   ├── CanvasPanel.jsx  # Custom HTML rendering
│   ├── ChatArea.jsx     # Main chat interface
│   ├── Header.jsx       # Application header
│   ├── MarketplacePanel.jsx # MCP server selection
│   ├── Message.jsx      # Individual chat message
│   ├── RagPanel.jsx     # RAG configuration
│   ├── Sidebar.jsx      # Navigation sidebar
│   ├── ToolsPanel.jsx   # MCP tools interface
│   └── WelcomeScreen.jsx # Initial welcome screen
├── contexts/            # React contexts
│   ├── ChatContext.jsx  # Chat state management
│   ├── MarketplaceContext.jsx # MCP marketplace state
│   └── WSContext.jsx    # WebSocket connection
├── App.jsx             # Main application component
└── main.jsx            # Application entry point
```

### State Management

The application uses React Context for state management:

**ChatContext**: Manages chat messages, models, and UI state
```jsx
const {
  messages,
  currentModel,
  isCanvasOpen,
  customContent,
  sendMessage,
  setCurrentModel
} = useChat();
```

**WSContext**: Handles WebSocket connection and communication
```jsx
const {
  socket,
  isConnected,
  sendMessage,
  connectionStatus
} = useWebSocket();
```

**MarketplaceContext**: Manages MCP server selection
```jsx
const {
  selectedServers,
  availableServers,
  toggleServer
} = useMarketplace();
```

## Key Features

### Real-time Chat
- WebSocket-based communication with backend
- Support for multiple LLM models
- Real-time message streaming
- Message history management

### MCP Integration
- Interactive tool selection and execution
- Server marketplace for enabling/disabling MCP servers
- Authorization-based tool access
- Tool execution feedback

### Canvas Panel
- Custom HTML rendering from MCP servers
- DOMPurify sanitization for security
- Resizable panels for flexible layout
- Auto-opening when custom content is received

### Responsive Design
- Mobile-friendly layout
- Collapsible sidebar on mobile
- Adaptive component sizing
- Tailwind CSS responsive classes

## Development Setup

### Prerequisites
Make sure you have Node.js 18+ installed.

### Installation
```bash
cd frontend
npm install
```

### Development Commands
```bash
# Build for production (recommended)
npm run build

# Development server (has WebSocket issues - not recommended)
npm run dev

# Lint code
npm run lint

# Preview built application
npm run preview
```

**Important**: Use `npm run build` instead of `npm run dev` for development, as the dev server has WebSocket connection issues.

## Component Development

### Creating New Components

1. **Create component file** in `src/components/`:
   ```jsx
   import React from 'react';
   
   const MyComponent = ({ prop1, prop2 }) => {
     return (
       <div className="p-4">
         <h2 className="text-lg font-bold">{prop1}</h2>
         <p>{prop2}</p>
       </div>
     );
   };
   
   export default MyComponent;
   ```

2. **Import and use** in parent component:
   ```jsx
   import MyComponent from './components/MyComponent';
   
   function App() {
     return (
       <MyComponent prop1="Title" prop2="Description" />
     );
   }
   ```

### Styling Guidelines

Use Tailwind CSS classes for styling:
```jsx
// Good: Tailwind classes
<div className="bg-gray-800 text-white p-4 rounded-lg shadow-md">
  <h2 className="text-xl font-bold mb-2">Title</h2>
  <p className="text-gray-300">Description</p>
</div>

// Avoid: Inline styles
<div style={{backgroundColor: '#1f2937', color: 'white'}}>
```

### WebSocket Communication

Send messages to backend:
```jsx
import { useWebSocket } from '../contexts/WSContext';

const MyComponent = () => {
  const { sendMessage } = useWebSocket();
  
  const handleAction = () => {
    sendMessage({
      type: 'chat',
      content: 'Hello, world!',
      model: 'gpt-4',
      user: 'user@example.com'
    });
  };
  
  return <button onClick={handleAction}>Send Message</button>;
};
```

Handle incoming messages:
```jsx
import { useChat } from '../contexts/ChatContext';

const MyComponent = () => {
  const { messages } = useChat();
  
  useEffect(() => {
    // Listen for new messages
    const latestMessage = messages[messages.length - 1];
    if (latestMessage?.type === 'custom_response') {
      // Handle custom response
    }
  }, [messages]);
  
  return <div>...</div>;
};
```

## Working with MCP Servers

### Tool Execution
```jsx
const executeTool = (serverName, toolName, arguments) => {
  sendMessage({
    type: 'mcp_request',
    server: serverName,
    request: {
      method: 'tools/call',
      params: {
        name: toolName,
        arguments: arguments
      }
    }
  });
};
```

### Custom HTML Rendering
MCP servers can return custom HTML that's rendered in the Canvas panel:
```jsx
// In CanvasPanel.jsx
const renderCustomHTML = (htmlContent) => {
  const sanitizedHTML = DOMPurify.sanitize(htmlContent);
  return <div dangerouslySetInnerHTML={{ __html: sanitizedHTML }} />;
};
```

## Testing Frontend Components

### Basic Component Testing
Create test files alongside components:
```jsx
// MyComponent.test.jsx
import { render, screen } from '@testing-library/react';
import MyComponent from './MyComponent';

test('renders component correctly', () => {
  render(<MyComponent prop1="Test" prop2="Description" />);
  expect(screen.getByText('Test')).toBeInTheDocument();
});
```

### Testing with Context
```jsx
import { ChatProvider } from '../contexts/ChatContext';

test('component with context', () => {
  render(
    <ChatProvider>
      <MyComponent />
    </ChatProvider>
  );
});
```

## Build Process

### Vite Configuration
The project uses Vite for building. Key configuration in `vite.config.js`:
```js
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom']
        }
      }
    }
  }
});
```

### Build Output
- Built files go to `frontend/dist/`
- Backend serves these static files
- Single-page application with routing

## Common Development Tasks

### Adding New Pages
1. Create component in `src/components/`
2. Add route in `App.jsx` (if using React Router)
3. Update navigation in `Sidebar.jsx`

### Styling Updates
- Use Tailwind CSS classes
- Check `tailwind.config.js` for custom theme
- Run build to see changes

### API Integration
- Use WebSocket context for real-time communication
- Handle connection states appropriately
- Implement error handling for failed requests

## Troubleshooting

### Common Issues

1. **WebSocket connection fails**:
   - Check backend is running on port 8000
   - Verify WebSocket URL in WSContext

2. **Build fails**:
   - Run `npm install` to ensure dependencies
   - Check for TypeScript/JavaScript syntax errors

3. **Styling not working**:
   - Ensure Tailwind classes are correct
   - Check if PostCSS is configured properly

4. **Components not rendering**:
   - Check browser console for JavaScript errors
   - Verify import/export statements

### Debug Commands
```bash
# Check dependencies
npm list

# Clear node_modules and reinstall
rm -rf node_modules package-lock.json
npm install

# Build with verbose output
npm run build --verbose
```

## Performance Considerations

- Use React.memo for expensive components
- Implement proper key props for lists
- Lazy load components when appropriate
- Optimize WebSocket message handling to prevent re-renders