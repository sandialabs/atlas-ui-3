# Custom API Key Expansion Implementation Plan

**Date:** 2025-11-10
**Status:** Planning Phase
**Author:** Claude Code

## Executive Summary

This document outlines a plan to implement flexible, extensible API key expansion in `llmconfig.yml`, replacing the current inconsistent approach with a unified, plugin-based system that supports multiple secret sources.

---

## Current State Analysis

### Existing Implementation

**Two Different Expansion Methods:**

1. **`resolve_env_var()` in `backend/modules/config/config_manager.py:25-63`**
   - Pattern: `${VAR_NAME}` only
   - Behavior: Fail-fast (raises `ValueError` if var missing)
   - Used by: MCP server `auth_token` fields
   - Limitation: Only matches patterns at string start (uses `re.match()`)

2. **`os.path.expandvars()` in `backend/modules/llm/litellm_caller.py:80`**
   - Pattern: `$VAR` or `${VAR}`, supports partial patterns
   - Behavior: Silent failure (returns unchanged string)
   - Used by: LLM `api_key` fields
   - Issue: Allows invalid keys like `"${OPENAI_API_KEY}"` to pass through

### Problems with Current Approach

1. **Inconsistency:** Different expansion logic for MCP vs LLM configs
2. **Limited Flexibility:** Only supports environment variables
3. **Silent Failures:** `os.path.expandvars()` doesn't validate results
4. **No Extensibility:** Can't add new secret sources (files, Vault, AWS Secrets Manager)
5. **Pattern Limitation:** `resolve_env_var()` only matches from string start

---

## Goals

### Primary Objectives

1. **Unify** expansion logic across all config types (LLM, MCP, future configs)
2. **Extend** support beyond environment variables to multiple secret sources
3. **Maintain** backward compatibility with existing `${ENV_VAR}` syntax
4. **Enable** plugin architecture for custom expansion strategies
5. **Improve** error handling with clear, actionable messages

### Non-Goals

- Replacing existing `.env` file management
- Adding encryption/decryption (secrets should be managed externally)
- Building a full secret management service

---

## Proposed Solution

### Architecture: Strategy Pattern with Resolver Chain

```
┌─────────────────────────────────────────────────────────┐
│ SecretResolver (Orchestrator)                           │
│  - Manages resolver chain                               │
│  - Fallback logic                                       │
│  - Error aggregation                                    │
└─────────────┬───────────────────────────────────────────┘
              │
              ├── EnvVarResolver (${ENV_VAR})
              ├── FileResolver (file:///path/to/secret)
              ├── VaultResolver (vault://secret/path)
              ├── AwsSecretsResolver (aws-sm://secret-name)
              └── CustomResolver (user-defined)
```

### Expansion Syntax Design

Support multiple URI-like schemes for different secret sources:

| Syntax | Description | Example | Status |
|--------|-------------|---------|--------|
| `${VAR}` | Environment variable | `${OPENAI_API_KEY}` | **Phase 1** (backward compat) |
| `env://VAR` | Explicit env var | `env://OPENAI_API_KEY` | **Phase 1** |
| `file://path` | File contents | `file:///etc/secrets/openai.key` | **Phase 2** |
| `vault://path` | HashiCorp Vault | `vault://secret/data/openai` | **Phase 3** (optional) |
| `aws-sm://name` | AWS Secrets Manager | `aws-sm://prod/openai-key` | **Phase 3** (optional) |
| `literal:value` | Literal string (escape) | `literal:${not-a-var}` | **Phase 2** |

---

## Implementation Plan

### Phase 1: Core Infrastructure (Essential)

**Objective:** Unify existing behavior, add plugin architecture

#### 1.1 Create Base Resolver Protocol

**New File:** `backend/modules/config/secret_resolvers.py`

```python
# Protocol definition (structural typing)
class SecretResolverProtocol(Protocol):
    """Protocol for secret resolution strategies."""

    def can_resolve(self, value: str) -> bool:
        """Check if this resolver can handle the value pattern."""
        ...

    def resolve(self, value: str) -> str:
        """
        Resolve the secret value.

        Raises:
            SecretResolutionError: If resolution fails
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable name for error messages."""
        ...
```

#### 1.2 Implement EnvVarResolver

Consolidates existing `resolve_env_var()` logic:

```python
class EnvVarResolver:
    """Resolves environment variable patterns: ${VAR} or env://VAR"""

    def can_resolve(self, value: str) -> bool:
        # Match ${VAR} or env://VAR
        return bool(re.match(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}$', value)) or \
               value.startswith('env://')

    def resolve(self, value: str) -> str:
        # Extract var name from ${VAR} or env://VAR
        # Lookup in os.environ
        # Raise SecretResolutionError if not found
```

#### 1.3 Create SecretResolver Orchestrator

**Manages resolver chain with fallback logic:**

```python
class SecretResolver:
    """
    Orchestrates multiple secret resolution strategies.
    Tries resolvers in order until one succeeds.
    """

    def __init__(self, resolvers: List[SecretResolverProtocol]):
        self._resolvers = resolvers

    def resolve(self, value: Optional[str],
                field_name: str = "api_key") -> Optional[str]:
        """
        Resolve secret using registered resolvers.

        Returns literal string if no resolver matches pattern.
        Raises SecretResolutionError with detailed context on failure.
        """
```

#### 1.4 Define Custom Exception

```python
class SecretResolutionError(Exception):
    """Raised when secret resolution fails."""

    def __init__(self,
                 value: str,
                 field_name: str,
                 attempted_resolvers: List[str],
                 errors: Dict[str, str]):
        # Clear, actionable error message
        # Include which resolvers were tried
        # Suggest fixes (check .env, file permissions, etc.)
```

#### 1.5 Update ModelConfig Validation

**File:** `backend/modules/config/config_manager.py`

Add Pydantic field validator to resolve API keys at config load time:

```python
class ModelConfig(BaseModel):
    model_name: str
    model_url: str
    api_key: str  # Raw value from YAML
    # ... other fields

    @field_validator('api_key', mode='before')
    @classmethod
    def resolve_api_key(cls, v: str) -> str:
        """Resolve API key using SecretResolver."""
        resolver = get_default_secret_resolver()  # Singleton
        return resolver.resolve(v, field_name="api_key")
```

**Benefits:**
- API keys resolved once at startup
- Validation errors prevent bad configs from loading
- Downstream code works with resolved values

#### 1.6 Standardize litellm_caller.py

**File:** `backend/modules/llm/litellm_caller.py:79-92`

**Change:** Remove `os.path.expandvars()` call since `ModelConfig` now stores resolved values:

```python
def _get_model_kwargs(self, model_name: str, ...) -> Dict[str, Any]:
    model_config = self.llm_config.models[model_name]

    # API key already resolved by ModelConfig validator
    api_key = model_config.api_key  # Already a plain string

    if api_key:  # No need to check for "${" prefix
        # Set env vars for LiteLLM
        ...
```

#### 1.7 Update MCP Client

**File:** `backend/modules/mcp_tools/client.py:117-123`

Replace direct `resolve_env_var()` call with `SecretResolver`:

```python
raw_token = config.get("auth_token")
try:
    resolver = get_default_secret_resolver()
    token = resolver.resolve(raw_token, field_name="auth_token")
except SecretResolutionError as e:
    logger.error(f"Failed to resolve auth_token for {server_name}: {e}")
    return None
```

#### 1.8 Testing

**New File:** `backend/tests/test_secret_resolvers.py`

- Unit tests for each resolver
- Integration tests for SecretResolver orchestrator
- Error handling and edge cases
- Backward compatibility with existing configs

**Update:** `backend/tests/test_config_manager.py`

- Migrate `TestResolveEnvVar` tests to new system
- Add tests for Pydantic validator integration

**Update:** `backend/tests/test_litellm_caller.py`

- Verify resolved API keys work correctly
- Test error handling for missing secrets

---

### Phase 2: File-Based Secrets (Optional, Recommended)

**Objective:** Support reading secrets from files

#### 2.1 Implement FileResolver

```python
class FileResolver:
    """Resolves file-based secrets: file:///path/to/secret"""

    def can_resolve(self, value: str) -> bool:
        return value.startswith('file://')

    def resolve(self, value: str) -> str:
        # Parse file:// URI
        # Read file contents
        # Strip whitespace/newlines
        # Validate file exists and is readable
        # Security: Restrict to specific directories?
```

**Use Case:** Docker secrets, Kubernetes mounted secrets

**Example:**
```yaml
models:
  gpt-4.1:
    api_key: "file:///run/secrets/openai_api_key"
```

#### 2.2 Implement LiteralResolver

```python
class LiteralResolver:
    """Escapes literal values: literal:${not-a-var}"""

    def can_resolve(self, value: str) -> bool:
        return value.startswith('literal:')

    def resolve(self, value: str) -> str:
        # Return everything after 'literal:' prefix
        return value[8:]  # len('literal:') == 8
```

**Use Case:** When you need to pass literal `${...}` to API

---

### Phase 3: Cloud Secret Managers (Optional, Advanced)

**Objective:** Enterprise secret management integration

#### 3.1 AWS Secrets Manager Resolver

```python
class AwsSecretsManagerResolver:
    """Resolves AWS Secrets Manager secrets: aws-sm://secret-name"""

    def __init__(self):
        # Lazy-load boto3 to avoid dependency if not used
        self._client = None

    def can_resolve(self, value: str) -> bool:
        return value.startswith('aws-sm://')

    def resolve(self, value: str) -> str:
        # Parse secret name from URI
        # Use boto3 to fetch secret
        # Handle IAM permissions errors
        # Cache results (with TTL)
```

**Configuration:**
```yaml
models:
  gpt-4.1:
    api_key: "aws-sm://prod/openai-api-key"
```

**Dependencies:** Requires `boto3` (optional dependency)

#### 3.2 HashiCorp Vault Resolver

```python
class VaultResolver:
    """Resolves HashiCorp Vault secrets: vault://secret/data/path"""

    def __init__(self, vault_addr: str, vault_token: str):
        # Initialize hvac client
        ...

    def can_resolve(self, value: str) -> bool:
        return value.startswith('vault://')

    def resolve(self, value: str) -> str:
        # Parse secret path from URI
        # Fetch from Vault
        # Handle auth errors
```

**Configuration:** Via environment variables:
```bash
VAULT_ADDR=https://vault.example.com
VAULT_TOKEN=s.xxxxxx
```

**Dependencies:** Requires `hvac` (optional dependency)

---

## Configuration Management

### Resolver Registration

**File:** `backend/modules/config/config_manager.py`

Add resolver configuration:

```python
def get_default_secret_resolver() -> SecretResolver:
    """
    Create default SecretResolver with standard resolvers.

    Resolvers are tried in order. Add custom resolvers via plugin system.
    """
    resolvers = [
        EnvVarResolver(),
        LiteralResolver(),
    ]

    # Optional: Add file resolver if enabled
    if os.getenv("ENABLE_FILE_SECRETS", "true").lower() == "true":
        resolvers.append(FileResolver())

    # Optional: Add cloud resolvers if credentials available
    if os.getenv("AWS_SECRET_MANAGER_ENABLED") == "true":
        resolvers.append(AwsSecretsManagerResolver())

    if os.getenv("VAULT_ADDR"):
        resolvers.append(VaultResolver(
            vault_addr=os.environ["VAULT_ADDR"],
            vault_token=os.environ.get("VAULT_TOKEN", "")
        ))

    return SecretResolver(resolvers)
```

### Plugin System (Future)

Allow users to register custom resolvers:

**File:** `backend/modules/config/resolver_plugins.py`

```python
# Global registry
_custom_resolvers: List[SecretResolverProtocol] = []

def register_resolver(resolver: SecretResolverProtocol) -> None:
    """Register a custom secret resolver."""
    _custom_resolvers.append(resolver)

def get_custom_resolvers() -> List[SecretResolverProtocol]:
    """Get all registered custom resolvers."""
    return _custom_resolvers.copy()
```

**User Code:** In custom startup script

```python
from backend.modules.config.resolver_plugins import register_resolver

class MyCustomResolver:
    def can_resolve(self, value: str) -> bool:
        return value.startswith('custom://')

    def resolve(self, value: str) -> str:
        # Custom logic
        ...

register_resolver(MyCustomResolver())
```

---

## Migration Strategy

### Backward Compatibility

**All existing configs continue to work without changes:**

```yaml
# Current syntax - STILL WORKS
models:
  gpt-4.1:
    api_key: "${OPENAI_API_KEY}"  # Resolved by EnvVarResolver
```

### Opt-In New Features

**Users can adopt new syntax incrementally:**

```yaml
# New syntax - explicitly use env://
models:
  gpt-4.1:
    api_key: "env://OPENAI_API_KEY"

# File-based secrets
  claude-3:
    api_key: "file:///run/secrets/anthropic_key"

# Cloud secrets (Phase 3)
  gemini:
    api_key: "aws-sm://prod/google-api-key"
```

### Deprecation Path

1. **Phase 1 Release:** Both `${VAR}` and `env://VAR` supported
2. **6 months:** Log warning if `${VAR}` used (suggest `env://VAR`)
3. **12 months:** Deprecate `${VAR}` in favor of explicit `env://VAR`
4. **18 months:** (Optional) Remove support for `${VAR}`

**Note:** Given backward compatibility priority, may keep `${VAR}` indefinitely

---

## Error Handling

### Clear, Actionable Error Messages

**Example Error Output:**

```
SecretResolutionError: Failed to resolve api_key for model 'gpt-4.1'

Value: "${OPENAI_API_KEY}"
Field: api_key

Attempted resolvers:
  1. EnvVarResolver - FAILED: Environment variable 'OPENAI_API_KEY' not set
  2. FileResolver - SKIPPED: Pattern does not match file:// scheme

Suggestions:
  - Set environment variable OPENAI_API_KEY in your .env file
  - Use 'file://' scheme to read from a file: file:///path/to/secret
  - Use 'literal:' prefix if this is meant to be a literal value

For more information, see: https://docs.atlas-ui.example/secrets
```

### Graceful Degradation

**For MCP servers:** Skip server if auth_token resolution fails (current behavior)

**For LLM models:** Fail at startup (prevents silent runtime failures)

---

## Security Considerations

### 1. File Resolver Restrictions

**Option A:** Restrict to specific directories
```python
ALLOWED_SECRET_DIRS = [
    "/run/secrets",      # Docker secrets
    "/var/secrets",      # Kubernetes secrets
    "~/.config/secrets"  # User secrets
]
```

**Option B:** Opt-in via environment variable
```bash
ALLOW_FILE_SECRETS=true
FILE_SECRETS_BASE_PATH=/run/secrets
```

### 2. Secret Logging

**Never log resolved secrets:**

```python
# Good
logger.info(f"Resolving api_key for model '{model_name}'")

# BAD - leaks secret
logger.info(f"Resolved api_key: {api_key}")
```

**Implement:** Redaction in logging middleware

### 3. Error Messages

**Don't leak partial secret values in errors:**

```python
# Good
raise SecretResolutionError(f"Failed to resolve {field_name}")

# BAD - might leak partial secret
raise SecretResolutionError(f"Failed to resolve '{value[:20]}...'")
```

### 4. File Permissions

**FileResolver should validate:**
- File is readable by current user
- File is not world-readable (warn if 0o644)
- File owner matches process user (optional)

### 5. Caching

**For cloud resolvers (AWS, Vault):**
- Cache resolved secrets to reduce API calls
- Implement TTL (default: 5 minutes)
- Clear cache on SIGHUP or via admin endpoint

**Security tradeoff:** Cached secrets stay in memory longer

---

## Performance Considerations

### Startup Time

**Impact:** Resolving secrets adds latency at config load time

**Mitigation:**
1. Lazy-load cloud resolver clients (boto3, hvac)
2. Parallel resolution for multiple secrets (use `asyncio` or `concurrent.futures`)
3. Cache results between hot-reloads (development only)

### Runtime Performance

**No impact:** Secrets resolved once at startup, not per-request

### Memory

**Cloud resolvers with caching:** +10-50MB per cached secret (depends on implementation)

---

## Testing Strategy

### Unit Tests

**File:** `backend/tests/test_secret_resolvers.py`

- Test each resolver in isolation
- Mock external dependencies (AWS, Vault)
- Edge cases: missing files, permission errors, malformed URIs
- Performance: Benchmark resolution time

### Integration Tests

**File:** `backend/tests/test_config_integration.py`

- Load llmconfig.yml with various secret patterns
- Verify ModelConfig validation
- Test error propagation
- Test resolver chain ordering

### End-to-End Tests

**File:** `test/e2e/test_secret_expansion_e2e.py`

- Real LiteLLM calls with resolved API keys
- MCP server connections with resolved auth tokens
- Error handling in full application context

### Manual Testing

**Checklist:**
1. Start app with `${ENV_VAR}` syntax (backward compat)
2. Start app with `env://VAR` syntax
3. Start app with `file://` syntax (Phase 2)
4. Missing env var triggers clear error
5. Missing file triggers clear error
6. Invalid URI scheme triggers clear error

---

## Documentation Updates

### Files to Update

1. **README.md**
   - Add section on API key configuration
   - Link to detailed docs

2. **New File:** `docs/configuration/secrets-management.md`
   - Complete guide to all secret resolution methods
   - Examples for each resolver
   - Troubleshooting guide
   - Security best practices

3. **.env.example**
   - Add comments explaining `${VAR}` syntax
   - Add examples of new syntax

4. **config/defaults/llmconfig.yml**
   - Add comments showing both syntaxes
   - Examples of file:// and cloud schemes

5. **CLAUDE.md**
   - Update "Configuration Files" section
   - Document new secret resolver system

### Example Documentation Snippet

```markdown
## API Key Configuration

Atlas UI 3 supports multiple methods for providing API keys:

### Environment Variables (Recommended)

`.env` file:
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

`config/defaults/llmconfig.yml`:
models:
  gpt-4.1:
    api_key: "${OPENAI_API_KEY}"  # Legacy syntax
    # OR
    api_key: "env://OPENAI_API_KEY"  # Explicit syntax

### File-Based Secrets (Docker, Kubernetes)

models:
  gpt-4.1:
    api_key: "file:///run/secrets/openai_key"

File contents should be the raw API key (whitespace trimmed).

### Cloud Secret Managers (Enterprise)

AWS Secrets Manager:
models:
  gpt-4.1:
    api_key: "aws-sm://prod/openai-api-key"

HashiCorp Vault:
models:
  gpt-4.1:
    api_key: "vault://secret/data/openai"
```

---

## Implementation Phases Timeline

### Phase 1: Core Infrastructure (Week 1-2)

**Must-Have for MVP:**

- [ ] Create `secret_resolvers.py` with Protocol and EnvVarResolver
- [ ] Create `SecretResolver` orchestrator
- [ ] Add `SecretResolutionError` exception
- [ ] Update `ModelConfig` with Pydantic validator
- [ ] Standardize `litellm_caller.py`
- [ ] Update `mcp_tools/client.py`
- [ ] Write unit tests (90% coverage target)
- [ ] Update documentation

**Deliverable:** Unified, backward-compatible secret resolution for env vars

### Phase 2: File Secrets (Week 3)

**Nice-to-Have:**

- [ ] Implement `FileResolver`
- [ ] Implement `LiteralResolver`
- [ ] Add file security validations
- [ ] Write integration tests
- [ ] Update documentation with file examples

**Deliverable:** Support for Docker/K8s mounted secrets

### Phase 3: Cloud Resolvers (Week 4+)

**Optional, Enterprise Features:**

- [ ] Implement `AwsSecretsManagerResolver`
- [ ] Implement `VaultResolver`
- [ ] Add optional dependencies (boto3, hvac)
- [ ] Implement caching layer
- [ ] Write E2E tests with mocked cloud services
- [ ] Update documentation

**Deliverable:** Enterprise-grade secret management

---

## Risks and Mitigations

### Risk 1: Breaking Changes

**Risk:** New validation breaks existing configs with invalid env vars

**Mitigation:**
- Add config validation mode: `STRICT_SECRET_VALIDATION=false` (default true)
- Log warnings instead of failing in non-strict mode
- Provide migration tool to test configs

### Risk 2: Performance Degradation

**Risk:** Cloud resolver API calls slow down startup

**Mitigation:**
- Lazy-load cloud clients
- Implement aggressive caching
- Make cloud resolvers opt-in

### Risk 3: Security Vulnerabilities

**Risk:** FileResolver allows arbitrary file reads

**Mitigation:**
- Restrict to allowed directories
- Require explicit opt-in via env var
- Validate file permissions
- Security audit before Phase 2 release

### Risk 4: Increased Complexity

**Risk:** Too many options confuse users

**Mitigation:**
- Clear, tiered documentation (Basic → Advanced)
- Start with Phase 1 only, gather feedback
- Provide migration examples
- Default to simplest approach (env vars)

---

## Success Metrics

### Functionality

- [ ] All existing configs work without changes
- [ ] New syntax resolves correctly
- [ ] Clear error messages for misconfigurations
- [ ] No performance regression (startup time < +100ms)

### Code Quality

- [ ] 90%+ test coverage for new code
- [ ] No linting errors (`ruff check`)
- [ ] All existing tests pass
- [ ] Documentation complete and accurate

### User Experience

- [ ] Users can configure secrets in < 5 minutes
- [ ] Error messages lead to quick resolution
- [ ] No production incidents due to secret misconfigurations

---

## Open Questions

1. **Caching Strategy:** Should cloud resolver caching be on by default?
   - **Recommendation:** Yes, with 5-minute TTL

2. **File Resolver Security:** Allow arbitrary paths or restrict to specific dirs?
   - **Recommendation:** Restrict by default, opt-in for arbitrary paths

3. **Deprecation Timeline:** When to deprecate `${VAR}` syntax?
   - **Recommendation:** Never (too common, low maintenance cost)

4. **Plugin System Priority:** Implement in Phase 1 or defer?
   - **Recommendation:** Defer to Phase 4, wait for user demand

5. **Async Resolution:** Should resolvers support async/await?
   - **Recommendation:** Yes for Phase 3 (cloud resolvers benefit from asyncio)

---

## Appendix: File Structure

### New Files

```
backend/
  modules/
    config/
      secret_resolvers.py          # Core resolver implementations
      resolver_plugins.py          # Plugin registration (Phase 4)
  tests/
    test_secret_resolvers.py       # Unit tests
    test_config_integration.py     # Integration tests
test/
  e2e/
    test_secret_expansion_e2e.py   # End-to-end tests
docs/
  configuration/
    secrets-management.md          # User documentation
  plans/
    api-key-expansion-plan-2025-11-10.md  # This document
```

### Modified Files

```
backend/
  modules/
    config/
      config_manager.py            # Add ModelConfig validator
    llm/
      litellm_caller.py            # Remove os.path.expandvars
    mcp_tools/
      client.py                    # Use SecretResolver
  tests/
    test_config_manager.py         # Migrate tests
    test_litellm_caller.py         # Update tests
config/
  defaults/
    llmconfig.yml                  # Add syntax comments
.env.example                       # Add secret syntax examples
README.md                          # Add configuration section
CLAUDE.md                          # Update configuration docs
```

---

## Conclusion

This plan provides a clear path to implementing flexible, extensible API key expansion while maintaining backward compatibility and following Atlas UI 3's clean architecture principles.

**Recommended Approach:**

1. **Start with Phase 1** - Solves immediate consistency issues
2. **Evaluate Phase 2** - Based on user demand for file secrets
3. **Defer Phase 3** - Only if enterprise customers need cloud integration

The phased approach allows incremental delivery with clear milestones and manageable risk.

---

**Next Steps:**

1. Review this plan with stakeholders
2. Validate assumptions with user interviews (if applicable)
3. Create GitHub issues for Phase 1 tasks
4. Begin implementation with `secret_resolvers.py`
