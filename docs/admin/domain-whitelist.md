# Email Domain Whitelist Configuration

Last updated: 2026-01-19

This configuration controls which email domains are allowed to access the application.

## Overview

The domain whitelist feature allows you to restrict access to users with email addresses from specific domains. This is useful for:
- Restricting access to government organizations (DOE, NNSA, national labs)
- Limiting access to specific companies or institutions
- Implementing multi-tenant access control

## Configuration Files

### Default Configuration
Located at: `config/defaults/domain-whitelist.json`

Contains DOE and national laboratory domains as an example. This file should not be modified directly.

### Custom Configuration
To customize domains, create: `config/overrides/domain-whitelist.json`

The override file takes precedence over the default configuration.

## Configuration Format

```json
{
  "version": "1.0",
  "description": "Your description here",
  "enabled": true,
  "domains": [
    {
      "domain": "example.com",
      "description": "Example Corporation",
      "category": "Enterprise"
    },
    {
      "domain": "another-domain.org",
      "description": "Another Organization",
      "category": "Partner"
    }
  ],
  "subdomain_matching": true
}
```

### Fields

- **version**: Configuration schema version (currently "1.0")
- **description**: Human-readable description of this configuration
- **enabled**: Whether the whitelist is enforced (true/false)
  - Note: Even if true here, must also set `FEATURE_DOMAIN_WHITELIST_ENABLED=true` in environment
- **domains**: Array of domain objects
  - **domain**: The email domain (e.g., "example.com")
  - **description**: Optional description
  - **category**: Optional category for organization
- **subdomain_matching**: If true, subdomains are automatically allowed
  - Example: If "example.com" is whitelisted and subdomain_matching is true, then "user@mail.example.com" is also allowed

## Enabling the Feature

1. Create your custom configuration at `config/overrides/domain-whitelist.json`
2. Set `"enabled": true` in the config file
3. Set environment variable: `FEATURE_DOMAIN_WHITELIST_ENABLED=true`
4. Restart the application

## Example Configurations

### Example 1: DOE National Labs (Default)
```json
{
  "enabled": true,
  "domains": [
    {"domain": "doe.gov", "description": "Department of Energy"},
    {"domain": "sandia.gov", "description": "Sandia National Labs"},
    {"domain": "lanl.gov", "description": "Los Alamos National Lab"}
  ],
  "subdomain_matching": true
}
```

### Example 2: Corporate Domains
```json
{
  "enabled": true,
  "domains": [
    {"domain": "mycompany.com", "description": "My Company"},
    {"domain": "partner-company.org", "description": "Trusted Partner"}
  ],
  "subdomain_matching": true
}
```

### Example 3: Educational Institutions
```json
{
  "enabled": true,
  "domains": [
    {"domain": "university.edu", "description": "University"},
    {"domain": "research-institute.org", "description": "Research Institute"}
  ],
  "subdomain_matching": true
}
```

## Behavior

### When Enabled
- Users with email addresses from whitelisted domains can access the application
- Users with other email domains receive a 403 Forbidden error (API) or redirect (UI)
- Health check endpoint (`/api/health`) bypasses the check
- Authentication endpoint bypasses the check

### When Disabled
- All authenticated users can access the application regardless of email domain
- No domain filtering is performed

## Subdomain Matching

When `subdomain_matching` is `true`:
- `user@example.com` matches `example.com` ✓
- `user@mail.example.com` matches `example.com` ✓
- `user@dept.mail.example.com` matches `example.com` ✓

When `subdomain_matching` is `false`:
- `user@example.com` matches `example.com` ✓
- `user@mail.example.com` does NOT match `example.com` ✗

## Troubleshooting

### Issue: Users are being blocked unexpectedly
- Check that `enabled` is set correctly in the config file
- Verify `FEATURE_DOMAIN_WHITELIST_ENABLED` environment variable
- Check domain spelling in the config file (case-insensitive)
- Check if subdomain_matching is set as needed

### Issue: Configuration changes not taking effect
- Restart the application after changing config files
- Verify the override file is at `config/overrides/domain-whitelist.json`
- Check application logs for config loading errors

### Issue: Everyone can access (no filtering)
- Verify `FEATURE_DOMAIN_WHITELIST_ENABLED=true` in environment
- Check that `enabled: true` in the config file
- Restart the application after making changes

## Logging

The middleware logs helpful information:
- On startup: Number of domains loaded and enabled status
- On rejection: Domain that was rejected (for debugging)
- On error: Config loading errors

Check application logs for domain whitelist messages.
