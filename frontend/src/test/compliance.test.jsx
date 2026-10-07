/**
 * Comprehensive tests for compliance level filtering functionality
 */

import { describe, it, expect } from 'vitest';
import { isComplianceAccessible, classificationsOf } from '../utils/complianceAccess';

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

    // The real shared rule (utils/complianceAccess), not a local copy.
    const isAccessible = (userLevel, resourceLevel, levels) =>
      isComplianceAccessible(levels.levels, userLevel, resourceLevel);

    it('should allow Public to access only Public resources', () => {
      expect(isAccessible('Public', 'Public', complianceLevels)).toBe(true);
      expect(isAccessible('Public', 'HIPAA', complianceLevels)).toBe(false);
      expect(isAccessible('Public', 'External', complianceLevels)).toBe(false);
    });

    it('should not let allowed_with widen HIPAA to SOC2-only resources', () => {
      expect(isAccessible('HIPAA', 'HIPAA', complianceLevels)).toBe(true);
      // HIPAA lists SOC2 in allowed_with, but that no longer grants access.
      expect(isAccessible('HIPAA', 'SOC2', complianceLevels)).toBe(false);
      expect(isAccessible('HIPAA', ['SOC2'], complianceLevels)).toBe(false);
      expect(isAccessible('HIPAA', 'Public', complianceLevels)).toBe(false);
      expect(isAccessible('HIPAA', 'Internal', complianceLevels)).toBe(false);
    });

    it('should allow HIPAA on a resource whose classifications include HIPAA', () => {
      expect(isAccessible('HIPAA', ['SOC2', 'HIPAA'], complianceLevels)).toBe(true);
      expect(isAccessible('SOC2', ['SOC2', 'HIPAA'], complianceLevels)).toBe(true);
      expect(isAccessible('Public', ['SOC2', 'HIPAA'], complianceLevels)).toBe(false);
      expect(isAccessible('HIPAA', [], complianceLevels)).toBe(false);
    });

    it('should limit FedRAMP to resources classified for FedRAMP', () => {
      expect(isAccessible('FedRAMP', 'FedRAMP', complianceLevels)).toBe(true);
      expect(isAccessible('FedRAMP', 'SOC2', complianceLevels)).toBe(false);
      expect(isAccessible('FedRAMP', ['SOC2', 'FedRAMP'], complianceLevels)).toBe(true);
      expect(isAccessible('FedRAMP', 'HIPAA', complianceLevels)).toBe(false);
      expect(isAccessible('FedRAMP', 'Public', complianceLevels)).toBe(false);
    });

    it('should prevent HIPAA from accessing Public resources (security)', () => {
      // Critical security test - HIPAA should NOT access Public
      // Prevents PII leakage through public internet search
      expect(isAccessible('HIPAA', 'Public', complianceLevels)).toBe(false);
    });

    it('should enforce strict filtering when compliance level is selected', () => {
      // When user selects a compliance level, resources without compliance_level should NOT show
      // This prevents untagged Public resources from appearing in HIPAA sessions
      expect(isAccessible('HIPAA', null, complianceLevels)).toBe(false);
      expect(isAccessible('SOC2', null, complianceLevels)).toBe(false);
      expect(isAccessible('Public', null, complianceLevels)).toBe(false);
      
      // But when no compliance level is selected, all resources are accessible
      expect(isAccessible(null, 'HIPAA', complianceLevels)).toBe(true);
      expect(isAccessible(null, 'Public', complianceLevels)).toBe(true);
      expect(isAccessible(null, null, complianceLevels)).toBe(true);
    });

    it('should filter tools by their data classifications with strict mode', () => {
      const tools = [
        { name: 'public-tool', compliance_level: 'Public' },
        { name: 'internal-tool', compliance_level: 'Internal' },
        { name: 'soc2-tool', compliance_level: 'SOC2' },
        { name: 'soc2-hipaa-tool', allowed_data_classifications: ['SOC2', 'HIPAA'] },
        { name: 'hipaa-tool', compliance_level: 'HIPAA' },
        { name: 'no-compliance-tool', compliance_level: null }
      ];

      // Filter tools for HIPAA user - STRICT MODE (no untagged resources)
      const hipaaTools = tools.filter(tool =>
        isAccessible('HIPAA', classificationsOf(tool), complianceLevels)
      );

      expect(hipaaTools.map(t => t.name)).toContain('hipaa-tool');
      expect(hipaaTools.map(t => t.name)).toContain('soc2-hipaa-tool');
      expect(hipaaTools.map(t => t.name)).not.toContain('soc2-tool');
      expect(hipaaTools.map(t => t.name)).not.toContain('no-compliance-tool'); // STRICT MODE
      expect(hipaaTools.map(t => t.name)).not.toContain('public-tool');
      expect(hipaaTools.map(t => t.name)).not.toContain('internal-tool');
    });

    it('should filter tools for Public user with strict mode (only Public)', () => {
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
      expect(publicTools.map(t => t.name)).not.toContain('no-compliance-tool'); // STRICT MODE
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

  describe('Explicit Classification Security', () => {
    const levels = [
      { name: 'Public', allowed_with: ['Public'] },
      { name: 'SOC2', allowed_with: ['SOC2'] },
      { name: 'HIPAA', allowed_with: ['HIPAA', 'SOC2'] }
    ];

    it('should not grant access through allowed_with in either direction', () => {
      // allowed_with no longer widens access: HIPAA cannot reach SOC2-only
      // resources, and SOC2 cannot reach HIPAA-only ones.
      expect(isComplianceAccessible(levels, 'HIPAA', 'SOC2')).toBe(false);
      expect(isComplianceAccessible(levels, 'SOC2', 'HIPAA')).toBe(false);
      // Only a resource that lists the level is reachable under it.
      expect(isComplianceAccessible(levels, 'HIPAA', ['SOC2', 'HIPAA'])).toBe(true);
      expect(isComplianceAccessible(levels, 'SOC2', ['SOC2', 'HIPAA'])).toBe(true);
    });

    it('should prevent mixing data from different security environments', () => {
      // Public and HIPAA should NOT mix
      expect(isComplianceAccessible(levels, 'Public', 'HIPAA')).toBe(false);
      expect(isComplianceAccessible(levels, 'HIPAA', 'Public')).toBe(false);
    });
  });
});
