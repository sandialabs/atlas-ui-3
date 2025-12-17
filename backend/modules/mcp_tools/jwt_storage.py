"""
Secure JWT storage for MCP server authentication.

This module provides encrypted storage for user-uploaded JWTs that are associated
with specific MCP servers.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Dict, List
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class JWTStorage:
    """
    Encrypted storage for user-uploaded JWTs.
    
    JWTs are stored in encrypted files, one per MCP server, using Fernet encryption.
    The encryption key is derived from an environment variable or auto-generated.
    """
    
    def __init__(self, storage_dir: Optional[str] = None, encryption_key: Optional[str] = None):
        """
        Initialize JWT storage.
        
        Args:
            storage_dir: Directory to store encrypted JWT files. 
                        Defaults to JWT_STORAGE_DIR env var or ~/.atlas-ui-3/jwt-storage
            encryption_key: Fernet encryption key (base64-encoded). If not provided, uses
                          JWT_STORAGE_ENCRYPTION_KEY env var or generates a new key.
        """
        # Set storage directory from env var or default
        if storage_dir is None:
            storage_dir = os.environ.get("JWT_STORAGE_DIR", "~/.atlas-ui-3/jwt-storage")
        
        self.storage_dir = Path(os.path.expanduser(storage_dir))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"JWT storage directory: {self.storage_dir}")
        
        # Set up encryption key
        if encryption_key is None:
            encryption_key = os.environ.get("JWT_STORAGE_ENCRYPTION_KEY")
        
        if encryption_key is None:
            # Auto-generate key and save to file for persistence
            key_file = self.storage_dir / ".encryption_key"
            if key_file.exists():
                encryption_key = key_file.read_text().strip()
                logger.info("Loaded existing JWT storage encryption key")
            else:
                encryption_key = Fernet.generate_key().decode()
                key_file.write_text(encryption_key)
                key_file.chmod(0o600)  # Secure permissions
                logger.warning(
                    "Generated new JWT storage encryption key. "
                    "Set JWT_STORAGE_ENCRYPTION_KEY env var for production use."
                )
        
        # Ensure encryption key is bytes for Fernet
        if isinstance(encryption_key, str):
            encryption_key_bytes = encryption_key.encode()
        else:
            encryption_key_bytes = encryption_key
        
        self.fernet = Fernet(encryption_key_bytes)
        logger.info(f"JWT storage initialized at {self.storage_dir}")
    
    def _get_jwt_path(self, server_name: str) -> Path:
        """Get path to JWT file for a server."""
        # Sanitize server name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in server_name)
        return self.storage_dir / f"{safe_name}.jwt.enc"
    
    def store_jwt(self, server_name: str, jwt_token: str) -> None:
        """
        Store JWT token for a server with encryption.
        
        Args:
            server_name: Name of the MCP server
            jwt_token: JWT token to store
        """
        jwt_path = self._get_jwt_path(server_name)
        
        # Encrypt the JWT
        encrypted_data = self.fernet.encrypt(jwt_token.encode())
        
        # Write to file with secure permissions
        jwt_path.write_bytes(encrypted_data)
        jwt_path.chmod(0o600)
        
        logger.info(f"Stored encrypted JWT for server: {server_name}")
    
    def get_jwt(self, server_name: str) -> Optional[str]:
        """
        Retrieve JWT token for a server.
        
        Args:
            server_name: Name of the MCP server
            
        Returns:
            Decrypted JWT token, or None if not found
        """
        jwt_path = self._get_jwt_path(server_name)
        
        if not jwt_path.exists():
            return None
        
        try:
            # Read and decrypt
            encrypted_data = jwt_path.read_bytes()
            jwt_token = self.fernet.decrypt(encrypted_data).decode()
            logger.debug(f"Retrieved JWT for server: {server_name}")
            return jwt_token
        except Exception as e:
            logger.error(f"Failed to decrypt JWT for {server_name}: {e}")
            return None
    
    def delete_jwt(self, server_name: str) -> bool:
        """
        Delete JWT token for a server.
        
        Args:
            server_name: Name of the MCP server
            
        Returns:
            True if deleted, False if not found
        """
        jwt_path = self._get_jwt_path(server_name)
        
        if not jwt_path.exists():
            return False
        
        jwt_path.unlink()
        logger.info(f"Deleted JWT for server: {server_name}")
        return True
    
    def has_jwt(self, server_name: str) -> bool:
        """
        Check if JWT exists for a server.
        
        Args:
            server_name: Name of the MCP server
            
        Returns:
            True if JWT exists, False otherwise
        """
        return self._get_jwt_path(server_name).exists()
    
    def list_servers_with_jwt(self) -> List[str]:
        """
        List all servers that have stored JWTs.
        
        Returns:
            List of server names
        """
        servers = []
        for jwt_file in self.storage_dir.glob("*.jwt.enc"):
            # Extract server name from filename
            server_name = jwt_file.stem.replace(".jwt", "")
            servers.append(server_name)
        return servers


# Global JWT storage instance
_jwt_storage: Optional[JWTStorage] = None


def get_jwt_storage() -> JWTStorage:
    """Get or create global JWT storage instance."""
    global _jwt_storage
    if _jwt_storage is None:
        _jwt_storage = JWTStorage()
    return _jwt_storage
