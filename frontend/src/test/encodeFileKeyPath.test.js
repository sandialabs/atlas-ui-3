import { describe, it, expect } from 'vitest'
import encodeFileKeyPath from '../utils/encodeFileKeyPath'

describe('encodeFileKeyPath', () => {
  it('preserves forward slashes as path separators', () => {
    expect(encodeFileKeyPath('users/alice/file.txt')).toBe('users/alice/file.txt')
  })

  it('encodes @ within path segments', () => {
    expect(encodeFileKeyPath('users/alice@example.com/file.txt'))
      .toBe('users/alice%40example.com/file.txt')
  })

  it('encodes spaces within segments', () => {
    expect(encodeFileKeyPath('users/alice/my file.txt'))
      .toBe('users/alice/my%20file.txt')
  })

  it('encodes special characters per segment', () => {
    expect(encodeFileKeyPath('users/alice@x.com/generated/report (1).txt'))
      .toBe('users/alice%40x.com/generated/report%20(1).txt')
  })

  it('handles keys with no slashes', () => {
    expect(encodeFileKeyPath('simple.txt')).toBe('simple.txt')
  })

  it('handles empty string', () => {
    expect(encodeFileKeyPath('')).toBe('')
  })

  it('encodes percent literal correctly', () => {
    expect(encodeFileKeyPath('users/alice/50%off.txt'))
      .toBe('users/alice/50%25off.txt')
  })

  it('encodes hash and question mark within segments', () => {
    expect(encodeFileKeyPath('users/alice/notes#1.txt'))
      .toBe('users/alice/notes%231.txt')
    expect(encodeFileKeyPath('users/alice/data?v=2.json'))
      .toBe('users/alice/data%3Fv%3D2.json')
  })

  it('preserves already-safe characters like hyphen and underscore', () => {
    expect(encodeFileKeyPath('users/alice/my-file_v2.txt'))
      .toBe('users/alice/my-file_v2.txt')
  })
})
