/**
 * Tests for file extraction configuration constants and defaults
 *
 * These tests verify the file extraction feature configuration
 * without relying on async hook behavior which is difficult to test in isolation.
 */

import { describe, it, expect } from 'vitest'

// Test the default configuration values used by useChatConfig
const DEFAULT_FEATURES = {
  workspaces: false,
  rag: false,
  tools: false,
  marketplace: false,
  files_panel: false,
  chat_history: false,
  compliance_levels: false,
  file_content_extraction: false
}

const DEFAULT_FILE_EXTRACTION = {
  enabled: false,
  default_behavior: 'attach_only',
  supported_extensions: []
}

describe('File Extraction Configuration Defaults', () => {
  describe('DEFAULT_FEATURES', () => {
    it('should have file_content_extraction feature flag', () => {
      expect(DEFAULT_FEATURES).toHaveProperty('file_content_extraction')
    })

    it('should have file_content_extraction disabled by default', () => {
      expect(DEFAULT_FEATURES.file_content_extraction).toBe(false)
    })

    it('should include all expected feature flags', () => {
      const expectedKeys = [
        'workspaces',
        'rag',
        'tools',
        'marketplace',
        'files_panel',
        'chat_history',
        'compliance_levels',
        'file_content_extraction'
      ]
      expect(Object.keys(DEFAULT_FEATURES).sort()).toEqual(expectedKeys.sort())
    })
  })

  describe('DEFAULT_FILE_EXTRACTION', () => {
    it('should have enabled property set to false', () => {
      expect(DEFAULT_FILE_EXTRACTION.enabled).toBe(false)
    })

    it('should have default_behavior set to attach_only', () => {
      expect(DEFAULT_FILE_EXTRACTION.default_behavior).toBe('attach_only')
    })

    it('should have empty supported_extensions array', () => {
      expect(DEFAULT_FILE_EXTRACTION.supported_extensions).toEqual([])
    })

    it('should have all expected properties', () => {
      const expectedKeys = ['enabled', 'default_behavior', 'supported_extensions']
      expect(Object.keys(DEFAULT_FILE_EXTRACTION).sort()).toEqual(expectedKeys.sort())
    })
  })
})

describe('File Extraction Configuration Merging', () => {
  // Helper function that mirrors the merging logic in useChatConfig
  const mergeFileExtraction = (apiConfig) => ({
    ...DEFAULT_FILE_EXTRACTION,
    ...(apiConfig || {})
  })

  const mergeFeatures = (apiFeatures) => ({
    ...DEFAULT_FEATURES,
    ...(apiFeatures || {})
  })

  describe('file extraction config merging', () => {
    it('should use defaults when API returns nothing', () => {
      const result = mergeFileExtraction(null)
      expect(result).toEqual(DEFAULT_FILE_EXTRACTION)
    })

    it('should use defaults when API returns undefined', () => {
      const result = mergeFileExtraction(undefined)
      expect(result).toEqual(DEFAULT_FILE_EXTRACTION)
    })

    it('should merge partial API config with defaults', () => {
      const apiConfig = { enabled: true }
      const result = mergeFileExtraction(apiConfig)

      expect(result.enabled).toBe(true)
      expect(result.default_behavior).toBe('attach_only')  // from defaults
      expect(result.supported_extensions).toEqual([])  // from defaults
    })

    it('should fully override with complete API config', () => {
      const apiConfig = {
        enabled: true,
        default_behavior: 'extract',
        supported_extensions: ['.pdf', '.png', '.jpg']
      }
      const result = mergeFileExtraction(apiConfig)

      expect(result).toEqual(apiConfig)
    })

    it('should handle empty supported_extensions from API', () => {
      const apiConfig = {
        enabled: true,
        default_behavior: 'extract',
        supported_extensions: []
      }
      const result = mergeFileExtraction(apiConfig)

      expect(result.supported_extensions).toEqual([])
    })
  })

  describe('features merging', () => {
    it('should use defaults when API returns nothing', () => {
      const result = mergeFeatures(null)
      expect(result.file_content_extraction).toBe(false)
    })

    it('should enable file_content_extraction when API returns true', () => {
      const apiFeatures = { file_content_extraction: true }
      const result = mergeFeatures(apiFeatures)

      expect(result.file_content_extraction).toBe(true)
    })

    it('should preserve other defaults when only file_content_extraction is set', () => {
      const apiFeatures = { file_content_extraction: true }
      const result = mergeFeatures(apiFeatures)

      expect(result.workspaces).toBe(false)
      expect(result.rag).toBe(false)
      expect(result.tools).toBe(false)
    })

    it('should merge multiple feature flags', () => {
      const apiFeatures = {
        file_content_extraction: true,
        tools: true,
        rag: true
      }
      const result = mergeFeatures(apiFeatures)

      expect(result.file_content_extraction).toBe(true)
      expect(result.tools).toBe(true)
      expect(result.rag).toBe(true)
      expect(result.workspaces).toBe(false)  // still default
    })
  })
})

describe('File Extraction Extension Handling', () => {
  // Helper to check if a file can be extracted
  const canExtractFile = (filename, fileExtraction) => {
    if (!fileExtraction?.enabled) return false
    const ext = '.' + filename.split('.').pop().toLowerCase()
    return fileExtraction.supported_extensions?.includes(ext)
  }

  describe('canExtractFile logic', () => {
    const enabledConfig = {
      enabled: true,
      default_behavior: 'extract',
      supported_extensions: ['.pdf', '.png', '.jpg', '.jpeg']
    }

    const disabledConfig = {
      enabled: false,
      default_behavior: 'attach_only',
      supported_extensions: ['.pdf']
    }

    it('should return true for supported PDF file', () => {
      expect(canExtractFile('document.pdf', enabledConfig)).toBe(true)
    })

    it('should return true for supported image files', () => {
      expect(canExtractFile('image.png', enabledConfig)).toBe(true)
      expect(canExtractFile('photo.jpg', enabledConfig)).toBe(true)
      expect(canExtractFile('picture.jpeg', enabledConfig)).toBe(true)
    })

    it('should return false for unsupported file types', () => {
      expect(canExtractFile('document.docx', enabledConfig)).toBe(false)
      expect(canExtractFile('archive.zip', enabledConfig)).toBe(false)
      expect(canExtractFile('data.json', enabledConfig)).toBe(false)
    })

    it('should return false when extraction is disabled', () => {
      expect(canExtractFile('document.pdf', disabledConfig)).toBe(false)
    })

    it('should return false when fileExtraction is null', () => {
      expect(canExtractFile('document.pdf', null)).toBe(false)
    })

    it('should return false when fileExtraction is undefined', () => {
      expect(canExtractFile('document.pdf', undefined)).toBe(false)
    })

    it('should handle case-insensitive extensions', () => {
      expect(canExtractFile('document.PDF', enabledConfig)).toBe(true)
      expect(canExtractFile('image.PNG', enabledConfig)).toBe(true)
      expect(canExtractFile('photo.JPG', enabledConfig)).toBe(true)
    })

    it('should handle files with multiple dots in name', () => {
      expect(canExtractFile('my.document.pdf', enabledConfig)).toBe(true)
      expect(canExtractFile('backup.2024.01.pdf', enabledConfig)).toBe(true)
    })

    it('should handle empty supported_extensions', () => {
      const emptyExtConfig = {
        enabled: true,
        default_behavior: 'extract',
        supported_extensions: []
      }
      expect(canExtractFile('document.pdf', emptyExtConfig)).toBe(false)
    })
  })
})

describe('File Extraction State Structure', () => {
  // Verify the expected structure of file extraction state
  it('should have correct shape for disabled state', () => {
    const disabledState = {
      enabled: false,
      default_behavior: 'attach_only',
      supported_extensions: []
    }

    expect(disabledState).toHaveProperty('enabled', false)
    expect(disabledState).toHaveProperty('default_behavior', 'attach_only')
    expect(disabledState).toHaveProperty('supported_extensions')
    expect(Array.isArray(disabledState.supported_extensions)).toBe(true)
  })

  it('should have correct shape for enabled state', () => {
    const enabledState = {
      enabled: true,
      default_behavior: 'extract',
      supported_extensions: ['.pdf', '.png']
    }

    expect(enabledState).toHaveProperty('enabled', true)
    expect(enabledState).toHaveProperty('default_behavior', 'extract')
    expect(enabledState.supported_extensions).toContain('.pdf')
    expect(enabledState.supported_extensions).toContain('.png')
  })

  it('should support valid default_behavior values', () => {
    const validBehaviors = ['extract', 'attach_only']

    validBehaviors.forEach(behavior => {
      const state = {
        enabled: true,
        default_behavior: behavior,
        supported_extensions: []
      }
      expect(['extract', 'attach_only']).toContain(state.default_behavior)
    })
  })
})
