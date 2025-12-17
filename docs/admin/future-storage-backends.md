# Future Storage Backend Architecture

## Overview

This document outlines the architecture for supporting alternative storage backends for JWT and OAuth tokens beyond the current filesystem-based approach.

## Current Implementation

**Storage Type:** Filesystem with Fernet encryption

**Location:** 
- JWT tokens: Configurable via `JWT_STORAGE_DIR` (default: `~/.atlas-ui-3/jwt-storage`)
- OAuth tokens: Per-server configurable via `oauth_config.token_storage_path`

**Structure:**
```
~/.atlas-ui-3/jwt-storage/
├── .encryption_key
├── username_server1.jwt.enc
├── username_server2.jwt.enc
└── admin_server3.jwt.enc
```

## Proposed Architecture

### Abstract Storage Interface

Create an abstract base class for all storage backends:

```python
from abc import ABC, abstractmethod
from typing import Optional, List

class TokenStorage(ABC):
    """Abstract base class for token storage backends."""
    
    @abstractmethod
    async def store_token(self, key: str, token: str, user: Optional[str] = None) -> None:
        """Store an encrypted token."""
        pass
    
    @abstractmethod
    async def get_token(self, key: str, user: Optional[str] = None) -> Optional[str]:
        """Retrieve and decrypt a token."""
        pass
    
    @abstractmethod
    async def delete_token(self, key: str, user: Optional[str] = None) -> bool:
        """Delete a token."""
        pass
    
    @abstractmethod
    async def has_token(self, key: str, user: Optional[str] = None) -> bool:
        """Check if a token exists."""
        pass
    
    @abstractmethod
    async def list_tokens(self, user: Optional[str] = None) -> List[str]:
        """List all token keys for a user."""
        pass
```

### Storage Backend Implementations

#### 1. Filesystem Storage (Current)

**Class:** `FilesystemTokenStorage`

**Configuration:**
```env
JWT_STORAGE_BACKEND=filesystem
JWT_STORAGE_DIR=~/.atlas-ui-3/jwt-storage
JWT_STORAGE_ENCRYPTION_KEY=base64-key
```

**Pros:**
- Simple, no external dependencies
- Works out of the box
- Good for single-server deployments

**Cons:**
- Not suitable for multi-server/distributed deployments
- No built-in backup/replication
- File system permissions management

#### 2. PostgreSQL Storage (Future)

**Class:** `PostgreSQLTokenStorage`

**Schema:**
```sql
CREATE TABLE mcp_tokens (
    id SERIAL PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    server_name VARCHAR(255) NOT NULL,
    token_type VARCHAR(50) NOT NULL,  -- 'jwt' or 'oauth'
    encrypted_token TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_email, server_name, token_type)
);

CREATE INDEX idx_mcp_tokens_user ON mcp_tokens(user_email);
CREATE INDEX idx_mcp_tokens_server ON mcp_tokens(server_name);
```

**Configuration:**
```env
JWT_STORAGE_BACKEND=postgresql
JWT_STORAGE_POSTGRES_URL=postgresql://user:pass@localhost/atlas
JWT_STORAGE_ENCRYPTION_KEY=base64-key
```

**Dependencies:**
```txt
asyncpg>=0.29.0
```

**Implementation Notes:**
- Tokens still encrypted before storage (defense in depth)
- Connection pooling for performance
- Automatic migration on startup
- Supports distributed deployments

**Pros:**
- Centralized storage for multi-server deployments
- Built-in backup/replication
- Query capabilities for admin tools
- ACID transactions

**Cons:**
- Requires PostgreSQL setup
- Additional operational complexity
- Network dependency

#### 3. Redis Storage (Future)

**Class:** `RedisTokenStorage`

**Configuration:**
```env
JWT_STORAGE_BACKEND=redis
JWT_STORAGE_REDIS_URL=redis://localhost:6379/0
JWT_STORAGE_ENCRYPTION_KEY=base64-key
JWT_STORAGE_REDIS_TTL=2592000  # 30 days in seconds
```

**Dependencies:**
```txt
redis[hiredis]>=5.0.0
```

**Key Structure:**
```
mcp:jwt:{user_email}:{server_name} = encrypted_token
mcp:oauth:{user_email}:{server_name} = encrypted_token
```

**Implementation Notes:**
- Automatic expiration via TTL
- Pub/sub for token invalidation
- Cluster support for high availability

**Pros:**
- Very fast read/write
- Built-in TTL/expiration
- Excellent for distributed systems
- Simple key-value model

**Cons:**
- In-memory (requires persistence config)
- Size limitations
- No complex queries

#### 4. AWS Secrets Manager (Future)

**Class:** `AWSSecretsManagerTokenStorage`

**Configuration:**
```env
JWT_STORAGE_BACKEND=aws_secrets
JWT_STORAGE_AWS_REGION=us-west-2
JWT_STORAGE_AWS_KMS_KEY_ID=alias/atlas-tokens
```

**Secret Naming:**
```
atlas/mcp/jwt/{user_email}/{server_name}
atlas/mcp/oauth/{user_email}/{server_name}
```

**Implementation Notes:**
- Uses AWS KMS for encryption (no manual key management)
- Automatic rotation support
- IAM-based access control

**Pros:**
- Managed encryption with KMS
- Automatic rotation
- Audit logging via CloudTrail
- No key management needed

**Cons:**
- AWS-specific
- Cost per secret
- API rate limits

## Implementation Plan

### Phase 1: Refactor Current Code
1. Extract `JWTStorage` into `FilesystemTokenStorage`
2. Create `TokenStorage` abstract base class
3. Update all callers to use abstract interface

### Phase 2: Add PostgreSQL Support
1. Implement `PostgreSQLTokenStorage`
2. Add migration scripts
3. Add configuration parsing
4. Add tests

### Phase 3: Add Redis Support
1. Implement `RedisTokenStorage`
2. Add cluster support
3. Add TTL configuration
4. Add tests

### Phase 4: Add AWS Support
1. Implement `AWSSecretsManagerTokenStorage`
2. Add IAM role support
3. Add rotation logic
4. Add tests

## Configuration

### Environment Variables

```env
# Storage backend selection
JWT_STORAGE_BACKEND=filesystem  # or postgresql, redis, aws_secrets

# Filesystem (default)
JWT_STORAGE_DIR=~/.atlas-ui-3/jwt-storage
JWT_STORAGE_ENCRYPTION_KEY=base64-key

# PostgreSQL
JWT_STORAGE_POSTGRES_URL=postgresql://user:pass@localhost/atlas
JWT_STORAGE_POSTGRES_POOL_SIZE=10
JWT_STORAGE_POSTGRES_TIMEOUT=30

# Redis
JWT_STORAGE_REDIS_URL=redis://localhost:6379/0
JWT_STORAGE_REDIS_TTL=2592000
JWT_STORAGE_REDIS_CLUSTER=false

# AWS Secrets Manager
JWT_STORAGE_AWS_REGION=us-west-2
JWT_STORAGE_AWS_KMS_KEY_ID=alias/atlas-tokens
```

### Factory Pattern

```python
def create_token_storage(backend: str = None) -> TokenStorage:
    """Create token storage based on configuration."""
    if backend is None:
        backend = os.environ.get("JWT_STORAGE_BACKEND", "filesystem")
    
    if backend == "filesystem":
        return FilesystemTokenStorage(
            storage_dir=os.environ.get("JWT_STORAGE_DIR"),
            encryption_key=os.environ.get("JWT_STORAGE_ENCRYPTION_KEY")
        )
    elif backend == "postgresql":
        return PostgreSQLTokenStorage(
            connection_url=os.environ.get("JWT_STORAGE_POSTGRES_URL"),
            encryption_key=os.environ.get("JWT_STORAGE_ENCRYPTION_KEY")
        )
    elif backend == "redis":
        return RedisTokenStorage(
            redis_url=os.environ.get("JWT_STORAGE_REDIS_URL"),
            encryption_key=os.environ.get("JWT_STORAGE_ENCRYPTION_KEY"),
            ttl=int(os.environ.get("JWT_STORAGE_REDIS_TTL", "2592000"))
        )
    elif backend == "aws_secrets":
        return AWSSecretsManagerTokenStorage(
            region=os.environ.get("JWT_STORAGE_AWS_REGION"),
            kms_key_id=os.environ.get("JWT_STORAGE_AWS_KMS_KEY_ID")
        )
    else:
        raise ValueError(f"Unknown storage backend: {backend}")
```

## Migration Strategy

### Database Migrations

For PostgreSQL backend, use Alembic for schema migrations:

```bash
# Create migration
alembic revision --autogenerate -m "Add MCP token storage"

# Apply migration
alembic upgrade head
```

### Data Migration

Tool to migrate from filesystem to database:

```python
async def migrate_tokens(
    source: TokenStorage,
    target: TokenStorage,
    dry_run: bool = False
):
    """Migrate tokens from one storage to another."""
    # Get all tokens from source
    all_tokens = await source.list_all_tokens()
    
    for token_key in all_tokens:
        token = await source.get_token(token_key)
        if not dry_run:
            await target.store_token(token_key, token)
            logger.info(f"Migrated token: {token_key}")
```

## Security Considerations

1. **Encryption at Rest**: All backends encrypt tokens before storage
2. **Encryption in Transit**: Use TLS for database connections
3. **Access Control**: Implement proper IAM/RBAC for each backend
4. **Key Rotation**: Support key rotation without token re-encryption
5. **Audit Logging**: Log all token operations

## Testing Strategy

1. **Unit Tests**: Test each storage backend independently
2. **Integration Tests**: Test with real databases (via Docker)
3. **Performance Tests**: Benchmark read/write operations
4. **Failover Tests**: Test behavior when storage is unavailable
5. **Migration Tests**: Verify data migration between backends

## Monitoring & Observability

### Metrics to Track

- Token storage/retrieval latency
- Storage backend errors
- Token expiration events
- Storage capacity usage

### Logging

Log all token operations with:
- User email
- Server name
- Operation type (store/get/delete)
- Success/failure status
- Timestamp

## Rollout Plan

1. Deploy filesystem backend (current)
2. Test with small user group
3. Add PostgreSQL backend
4. Gradual migration to PostgreSQL
5. Monitor for issues
6. Add other backends as needed

## References

- [FastMCP OAuth Documentation](https://gofastmcp.com/clients/auth/oauth)
- [PostgreSQL Encryption](https://www.postgresql.org/docs/current/encryption-options.html)
- [Redis Security](https://redis.io/docs/management/security/)
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/)
