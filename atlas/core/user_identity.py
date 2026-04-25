"""Utilities for comparing user identity values."""

from typing import Optional


def normalize_user_email(user_email: Optional[str]) -> str:
    """Normalize user emails for case-insensitive identity comparisons.

    Returns an empty string for missing or blank values. Callers that must
    reject missing identity should check for a truthy user value first.
    """
    return user_email.strip().lower() if user_email else ""
