"""Utilities for comparing user identity values."""

from typing import Optional


def normalize_user_email(user_email: Optional[str]) -> str:
    """Normalize user emails for case-insensitive identity comparisons."""
    return user_email.strip().lower() if user_email else ""
