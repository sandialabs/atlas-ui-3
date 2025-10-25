/**
 * Utility function tests - basic JavaScript functionality
 */

import { describe, it, expect } from 'vitest';

describe('Basic JavaScript Utilities', () => {
  it('should handle string formatting', () => {
    const template = (name, age) => `Hello ${name}, you are ${age} years old`;
    expect(template('John', 25)).toBe('Hello John, you are 25 years old');
  });

  it('should work with array operations', () => {
    const numbers = [1, 2, 3, 4, 5];
    const doubled = numbers.map(n => n * 2);
    const sum = doubled.reduce((a, b) => a + b, 0);
    
    expect(doubled).toEqual([2, 4, 6, 8, 10]);
    expect(sum).toBe(30);
  });

  it('should handle object operations', () => {
    const user = { name: 'Alice', age: 30 };
    const updatedUser = { ...user, age: 31 };
    
    expect(updatedUser.name).toBe('Alice');
    expect(updatedUser.age).toBe(31);
    expect(user.age).toBe(30); // Original unchanged
  });

  it('should work with JSON operations', () => {
    const data = { message: 'hello', count: 42 };
    const jsonString = JSON.stringify(data);
    const parsed = JSON.parse(jsonString);
    
    expect(parsed.message).toBe('hello');
    expect(parsed.count).toBe(42);
  });
});

describe('Chat UI Utility Functions', () => {
  it('should format file sizes', () => {
    const formatFileSize = (bytes) => {
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    expect(formatFileSize(500)).toBe('500 B');
    expect(formatFileSize(1024)).toBe('1.0 KB');
    expect(formatFileSize(1536)).toBe('1.5 KB');
    expect(formatFileSize(1048576)).toBe('1.0 MB');
  });

  it('should validate email format', () => {
    const isValidEmail = (email) => {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      return emailRegex.test(email);
    };

    expect(isValidEmail('user@example.com')).toBe(true);
    expect(isValidEmail('test.email@domain.co.uk')).toBe(true);
    expect(isValidEmail('invalid.email')).toBe(false);
    expect(isValidEmail('user@')).toBe(false);
    expect(isValidEmail('@domain.com')).toBe(false);
  });

  it('should truncate long text', () => {
    const truncateText = (text, maxLength) => {
      if (text.length <= maxLength) return text;
      return text.substring(0, maxLength - 3) + '...';
    };

    expect(truncateText('Short', 10)).toBe('Short');
    expect(truncateText('This is a very long text', 10)).toBe('This is...');
    expect(truncateText('12345678901', 10)).toBe('1234567...');
    expect(truncateText('Nine char', 10)).toBe('Nine char');
  });
});