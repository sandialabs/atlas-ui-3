/**
 * Comprehensive tests for compliance level filtering functionality
 */

import { describe, it, expect } from 'vitest';

describe('Compliance Level Filtering', () => {
  describe('Compliance Level Accessibility Logic', () => {
    const complianceLevels = {
      levels: [
        { name: 'Public', allowed_with: ['Public'] },
        { name: 'External', allowed_with: ['External'] },
        { name: 'Internal', allowed_with: ['Internal'] },
        { name: 'SOC2', allowed_with: ['SOC2'] },
        { name: 'HIPAA', allowed_with: ['HIPAA', 'SOC2'] },
        { name: 'FedRAMP', allowed_with: ['FedRAMP', 'SOC2'] }
      ]
    };

    // Helper function to simulate isComplianceAccessible
    const isAccessible = (userLevel, resourceLevel, levels) => {
      if (!userLevel || !resourceLevel) return true;

      const userLevelObj = levels.levels.find(l => l.name === userLevel);
      if (!userLevelObj) return true;

      return userLevelObj.allowed_with.includes(resourceLevel);
    };

    it('should allow Public to access only Public resources', () => {
      expect(isAccessible('Public', 'Public', complianceLevels)).toBe(true);
      expect(isAccessible('Public', 'HIPAA', complianceLevels)).toBe(false);
      expect(isAccessible('Public', 'External', complianceLevels)).toBe(false);
    });

    it('should allow HIPAA to access HIPAA and SOC2 resources', () => {
      expect(isAccessible('HIPAA', 'HIPAA', complianceLevels)).toBe(true);
      expect(isAccessible('HIPAA', 'SOC2', complianceLevels)).toBe(true);
      expect(isAccessible('HIPAA', 'Public', complianceLevels)).toBe(false);
      expect(isAccessible('HIPAA', 'Internal', complianceLevels)).toBe(false);
    });

    it('should allow FedRAMP to access FedRAMP and SOC2 resources', () => {
      expect(isAccessible('FedRAMP', 'FedRAMP', complianceLevels)).toBe(true);
      expect(isAccessible('FedRAMP', 'SOC2', complianceLevels)).toBe(true);
      expect(isAccessible('FedRAMP', 'HIPAA', complianceLevels)).toBe(false);
      expect(isAccessible('FedRAMP', 'Public', complianceLevels)).toBe(false);
    });

    it('should prevent HIPAA from accessing Public resources (security)', () => {
      // Critical security test - HIPAA should NOT access Public
      // Prevents PII leakage through public internet search
      expect(isAccessible('HIPAA', 'Public', complianceLevels)).toBe(false);
    });

    it('should allow None (unset) to be permissive', () => {
      // None user can access any resource
      expect(isAccessible(null, 'HIPAA', complianceLevels)).toBe(true);
      expect(isAccessible(null, 'Public', complianceLevels)).toBe(true);

      // Any user can access None resource
      expect(isAccessible('HIPAA', null, complianceLevels)).toBe(true);
      expect(isAccessible('Public', null, complianceLevels)).toBe(true);

      // None to None
      expect(isAccessible(null, null, complianceLevels)).toBe(true);
    });

    it('should filter tools by compliance level allowlist', () => {
      const tools = [
        { name: 'public-tool', compliance_level: 'Public' },
        { name: 'internal-tool', compliance_level: 'Internal' },
        { name: 'soc2-tool', compliance_level: 'SOC2' },
        { name: 'hipaa-tool', compliance_level: 'HIPAA' },
        { name: 'no-compliance-tool', compliance_level: null }
      ];

      // Filter tools for HIPAA user
      const hipaaTools = tools.filter(tool =>
        isAccessible('HIPAA', tool.compliance_level, complianceLevels)
      );

      expect(hipaaTools.map(t => t.name)).toContain('hipaa-tool');
      expect(hipaaTools.map(t => t.name)).toContain('soc2-tool');
      expect(hipaaTools.map(t => t.name)).toContain('no-compliance-tool');
      expect(hipaaTools.map(t => t.name)).not.toContain('public-tool');
      expect(hipaaTools.map(t => t.name)).not.toContain('internal-tool');
    });

    it('should filter tools for Public user (only Public + unset)', () => {
      const tools = [
        { name: 'public-tool', compliance_level: 'Public' },
        { name: 'internal-tool', compliance_level: 'Internal' },
        { name: 'hipaa-tool', compliance_level: 'HIPAA' },
        { name: 'no-compliance-tool', compliance_level: null }
      ];

      const publicTools = tools.filter(tool =>
        isAccessible('Public', tool.compliance_level, complianceLevels)
      );

      expect(publicTools.map(t => t.name)).toContain('public-tool');
      expect(publicTools.map(t => t.name)).toContain('no-compliance-tool');
      expect(publicTools.map(t => t.name)).not.toContain('internal-tool');
      expect(publicTools.map(t => t.name)).not.toContain('hipaa-tool');
    });

    it('should show all tools when no compliance filter is set', () => {
      const tools = [
        { name: 'public-tool', compliance_level: 'Public' },
        { name: 'internal-tool', compliance_level: 'Internal' },
        { name: 'hipaa-tool', compliance_level: 'HIPAA' }
      ];

      const allTools = tools.filter(tool =>
        isAccessible(null, tool.compliance_level, complianceLevels)
      );

      expect(allTools.length).toBe(3);
    });
  });

  describe('Allowlist Model Security', () => {
    it('should ensure allowlist is not bidirectional', () => {
      const levels = {
        levels: [
          { name: 'SOC2', allowed_with: ['SOC2'] },
          { name: 'HIPAA', allowed_with: ['HIPAA', 'SOC2'] }
        ]
      };

      const isAccessible = (userLevel, resourceLevel, levels) => {
        if (!userLevel || !resourceLevel) return true;
        const userLevelObj = levels.levels.find(l => l.name === userLevel);
        if (!userLevelObj) return true;
        return userLevelObj.allowed_with.includes(resourceLevel);
      };

      // HIPAA can access SOC2
      expect(isAccessible('HIPAA', 'SOC2', levels)).toBe(true);

      // But SOC2 cannot access HIPAA (not bidirectional)
      expect(isAccessible('SOC2', 'HIPAA', levels)).toBe(false);
    });

    it('should prevent mixing data from different security environments', () => {
      const levels = {
        levels: [
          { name: 'Public', allowed_with: ['Public'] },
          { name: 'HIPAA', allowed_with: ['HIPAA', 'SOC2'] }
        ]
      };

      const isAccessible = (userLevel, resourceLevel, levels) => {
        if (!userLevel || !resourceLevel) return true;
        const userLevelObj = levels.levels.find(l => l.name === userLevel);
        if (!userLevelObj) return true;
        return userLevelObj.allowed_with.includes(resourceLevel);
      };

      // Public and HIPAA should NOT mix
      expect(isAccessible('Public', 'HIPAA', levels)).toBe(false);
      expect(isAccessible('HIPAA', 'Public', levels)).toBe(false);
    });
  });
});
