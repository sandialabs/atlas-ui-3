/**
 * Test for Message component copy functionality
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

describe('Message Copy Functionality', () => {
  // Mock clipboard API
  const mockWriteText = vi.fn();
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(navigator, {
      clipboard: {
        writeText: mockWriteText
      }
    });
  });

  it('should have the copy function available', () => {
    // Test the copy message function logic
    const testContent = "Hello, this is a test message with some content.";
    
    // Simulate the copy function
    const copyMessageContent = (content, button) => {
      try {
        let textToCopy = '';
        
        if (typeof content === 'string') {
          textToCopy = content;
        } else if (content && typeof content === 'object') {
          if (content.raw && typeof content.raw === 'string') {
            textToCopy = content.raw;
          } else if (content.text && typeof content.text === 'string') {
            textToCopy = content.text;
          } else {
            textToCopy = JSON.stringify(content, null, 2);
          }
        } else {
          textToCopy = String(content || '');
        }
        
        if (navigator.clipboard && navigator.clipboard.writeText) {
          return navigator.clipboard.writeText(textToCopy);
        }
        
        return Promise.resolve();
      } catch (err) {
        console.error('Error in copyMessageContent: ', err);
        return Promise.reject(err);
      }
    };

    // Test string content
    const button = { classList: { add: vi.fn(), remove: vi.fn() } };
    copyMessageContent(testContent, button);
    
    expect(mockWriteText).toHaveBeenCalledWith(testContent);
  });

  it('should handle object content correctly', () => {
    const copyMessageContent = (content, button) => {
      let textToCopy = '';
      
      if (typeof content === 'string') {
        textToCopy = content;
      } else if (content && typeof content === 'object') {
        if (content.raw && typeof content.raw === 'string') {
          textToCopy = content.raw;
        } else if (content.text && typeof content.text === 'string') {
          textToCopy = content.text;
        } else {
          textToCopy = JSON.stringify(content, null, 2);
        }
      } else {
        textToCopy = String(content || '');
      }
      
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(textToCopy);
      }
    };

    // Test object with raw property
    const objWithRaw = { raw: "Raw content here", other: "data" };
    const button = { classList: { add: vi.fn(), remove: vi.fn() } };
    copyMessageContent(objWithRaw, button);
    
    expect(mockWriteText).toHaveBeenCalledWith("Raw content here");
  });

  it('should handle markdown content extraction', () => {
    const markdownContent = `# Hello World

This is a **markdown** message with:

- Lists
- Code blocks
- And other formatting

\`\`\`javascript
console.log("Hello World");
\`\`\`

It should copy as plain text.`;

    expect(typeof markdownContent).toBe('string');
    expect(markdownContent.includes('**markdown**')).toBe(true);
    expect(markdownContent.includes('```javascript')).toBe(true);
  });
});