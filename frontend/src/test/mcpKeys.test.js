import { describe, it, expect } from 'vitest'
import { findServerConfigForMcpKey, getMcpNameFromKey } from '../utils/mcpKeys'

describe('mcp key helpers', () => {
  const servers = [
    { server: 'file', compliance_level: 'Public' },
    { server: 'file_viewer', compliance_level: 'Internal' },
  ]

  it('matches the longest known server prefix', () => {
    expect(findServerConfigForMcpKey('file_viewer_custom_prompt', servers)).toEqual(servers[1])
  })

  it('extracts names for underscore server names', () => {
    expect(getMcpNameFromKey('file_viewer_custom_prompt', servers)).toBe('custom_prompt')
  })

  it('falls back to the legacy first underscore split for unknown servers', () => {
    expect(getMcpNameFromKey('unknown_prompt_name', servers)).toBe('prompt_name')
  })
})
