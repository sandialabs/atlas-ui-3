"""Content security check service for moderating user input and LLM output."""

import logging
from enum import Enum
from typing import Dict, List, Optional

import httpx

from modules.config import AppSettings

logger = logging.getLogger(__name__)


class SecurityCheckResult(str, Enum):
    """Security check result statuses."""
    BLOCKED = "blocked"
    ALLOWED_WITH_WARNINGS = "allowed-with-warnings"
    GOOD = "good"


class SecurityCheckResponse:
    """Response from security check API."""
    
    def __init__(
        self,
        status: SecurityCheckResult,
        message: Optional[str] = None,
        details: Optional[Dict] = None
    ):
        """
        Initialize security check response.
        
        Args:
            status: Security check status (blocked, allowed-with-warnings, good)
            message: Optional message explaining the result
            details: Optional additional details from the security check
        """
        self.status = status
        self.message = message
        self.details = details or {}
    
    def is_blocked(self) -> bool:
        """Check if content is blocked."""
        return self.status == SecurityCheckResult.BLOCKED
    
    def has_warnings(self) -> bool:
        """Check if content has warnings."""
        return self.status == SecurityCheckResult.ALLOWED_WITH_WARNINGS
    
    def is_good(self) -> bool:
        """Check if content is good."""
        return self.status == SecurityCheckResult.GOOD


class SecurityCheckService:
    """Service for performing content security checks."""
    
    def __init__(self, app_settings: AppSettings):
        """
        Initialize security check service.
        
        Args:
            app_settings: Application settings containing security check configuration
        """
        self.app_settings = app_settings
        self.api_url = app_settings.security_check_api_url
        self.api_key = app_settings.security_check_api_key
        self.timeout = app_settings.security_check_timeout
        self.input_enabled = app_settings.feature_security_check_input_enabled
        self.output_enabled = app_settings.feature_security_check_output_enabled
        self.tool_rag_enabled = app_settings.feature_security_check_tool_rag_enabled
    
    async def check_input(
        self,
        content: str,
        message_history: Optional[List[Dict]] = None,
        user_email: Optional[str] = None
    ) -> SecurityCheckResponse:
        """
        Check user input for security issues.
        
        Args:
            content: User input content to check
            message_history: Optional message history for context
            user_email: Optional user email for auditing
            
        Returns:
            SecurityCheckResponse with status and optional message
        """
        if not self.input_enabled:
            return SecurityCheckResponse(status=SecurityCheckResult.GOOD)
        
        if not self.api_url or not self.api_key:
            logger.warning(
                "Security check input is enabled but API URL or key is not configured. "
                "Allowing content by default."
            )
            return SecurityCheckResponse(status=SecurityCheckResult.GOOD)
        
        return await self._perform_check(
            content=content,
            check_type="input",
            message_history=message_history,
            user_email=user_email
        )
    
    async def check_output(
        self,
        content: str,
        message_history: Optional[List[Dict]] = None,
        user_email: Optional[str] = None
    ) -> SecurityCheckResponse:
        """
        Check LLM output for security issues.
        
        Args:
            content: LLM output content to check
            message_history: Optional message history for context
            user_email: Optional user email for auditing
            
        Returns:
            SecurityCheckResponse with status and optional message
        """
        if not self.output_enabled:
            return SecurityCheckResponse(status=SecurityCheckResult.GOOD)
        
        if not self.api_url or not self.api_key:
            logger.warning(
                "Security check output is enabled but API URL or key is not configured. "
                "Allowing content by default."
            )
            return SecurityCheckResponse(status=SecurityCheckResult.GOOD)
        
        return await self._perform_check(
            content=content,
            check_type="output",
            message_history=message_history,
            user_email=user_email
        )
    
    async def check_tool_rag_output(
        self,
        content: str,
        source_type: str,
        message_history: Optional[List[Dict]] = None,
        user_email: Optional[str] = None
    ) -> SecurityCheckResponse:
        """
        Check tool or RAG output for security issues before sending to LLM.
        
        This prevents malicious tool/RAG outputs from manipulating the LLM
        via prompt injection or other attacks.
        
        Args:
            content: Tool or RAG output content to check
            source_type: Type of source ("tool" or "rag")
            message_history: Optional message history for context
            user_email: Optional user email for auditing
            
        Returns:
            SecurityCheckResponse with status and optional message
        """
        if not self.tool_rag_enabled:
            return SecurityCheckResponse(status=SecurityCheckResult.GOOD)
        
        if not self.api_url or not self.api_key:
            logger.warning(
                "Security check for tool/RAG output is enabled but API URL or key is not configured. "
                "Allowing content by default."
            )
            return SecurityCheckResponse(status=SecurityCheckResult.GOOD)
        
        return await self._perform_check(
            content=content,
            check_type=f"tool_rag_{source_type}",
            message_history=message_history,
            user_email=user_email
        )
    
    async def _perform_check(
        self,
        content: str,
        check_type: str,
        message_history: Optional[List[Dict]] = None,
        user_email: Optional[str] = None
    ) -> SecurityCheckResponse:
        """
        Perform security check by calling external API.
        
        Args:
            content: Content to check
            check_type: Type of check ("input" or "output")
            message_history: Optional message history for context
            user_email: Optional user email for auditing
            
        Returns:
            SecurityCheckResponse with status and optional message
        """
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "content": content,
                    "check_type": check_type,
                    "username": user_email,
                    "message_history": message_history or []
                }
                
                logger.debug(
                    f"Performing {check_type} security check for user {user_email}, "
                    f"content length: {len(content)}"
                )
                
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                )
                
                response.raise_for_status()
                data = response.json()
                
                status_str = data.get("status", "good").lower()
                try:
                    status = SecurityCheckResult(status_str)
                except ValueError:
                    logger.error(
                        f"Invalid security check status received: {status_str}. "
                        "Defaulting to GOOD."
                    )
                    status = SecurityCheckResult.GOOD
                
                message = data.get("message")
                details = data.get("details", {})
                
                result = SecurityCheckResponse(
                    status=status,
                    message=message,
                    details=details
                )
                
                if result.is_blocked():
                    logger.warning(
                        f"Content blocked by {check_type} security check for user {user_email}: "
                        f"{message}"
                    )
                elif result.has_warnings():
                    logger.info(
                        f"Content allowed with warnings by {check_type} security check for user {user_email}: "
                        f"{message}"
                    )
                else:
                    logger.debug(f"{check_type.capitalize()} security check passed for user {user_email}")
                
                return result
                
        except httpx.HTTPStatusError as e:
            logger.error(
                f"Security check API returned error status {e.response.status_code}: {e}",
                exc_info=True
            )
            return SecurityCheckResponse(
                status=SecurityCheckResult.GOOD,
                message="Security check service temporarily unavailable. Content allowed by default."
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to security check API: {e}", exc_info=True)
            return SecurityCheckResponse(
                status=SecurityCheckResult.GOOD,
                message="Security check service temporarily unavailable. Content allowed by default."
            )
        except Exception as e:
            logger.error(f"Unexpected error during security check: {e}", exc_info=True)
            return SecurityCheckResponse(
                status=SecurityCheckResult.GOOD,
                message="Security check service error. Content allowed by default."
            )


def get_security_check_service(app_settings: AppSettings) -> SecurityCheckService:
    """
    Factory function to create security check service.
    
    Args:
        app_settings: Application settings
        
    Returns:
        SecurityCheckService instance
    """
    return SecurityCheckService(app_settings)
