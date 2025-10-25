/**
 * Basic test to verify test infrastructure is working
 */

import { describe, it, expect } from 'vitest';

describe('Basic Test Infrastructure', () => {
  it('should run a simple test', () => {
    expect(1 + 1).toBe(2);
  });

  it('should handle string operations', () => {
    expect('hello world'.split(' ')).toEqual(['hello', 'world']);
  });

  it('should work with arrays', () => {
    const arr = [1, 2, 3];
    expect(arr.length).toBe(3);
    expect(arr).toContain(2);
  });
});