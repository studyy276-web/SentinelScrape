"""Sanitization utilities for audit telemetry to prevent secret and credential leaks."""

import re
from typing import Any, Dict, List, Set

# Known sensitive keys that must always be masked
SENSITIVE_KEY_NAMES: Set[str] = {
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "authorization",
    "auth",
    "ws_endpoint",
    "customer_id",
    "bright_data_api_key",
    "bright_data_password",
    "bright_data_customer_id",
}


def sanitize_url(url: str) -> str:
    """Strips embedded user/passwords from HTTP and WebSocket URLs."""
    if not url:
        return ""
    # Redact http(s)://user:pass@host and wss://user:pass@host
    sanitized = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://***:***@", url)
    return sanitized


def sanitize_string(text: str) -> str:
    """Sanitizes text by stripping embedded credentials, Bearer tokens, and sensitive URLs."""
    if not text:
        return ""
    # Sanitize embedded URLs with credentials
    cleaned = sanitize_url(text)
    # Sanitize Bearer tokens
    cleaned = re.sub(r"Bearer\s+([A-Za-z0-9_\-\.]+)", r"Bearer ***", cleaned, flags=re.IGNORECASE)
    # Sanitize Bright Data zone credentials
    cleaned = re.sub(r"brd-customer-[A-Za-z0-9_\-]+-zone-[A-Za-z0-9_\-]+:[A-Za-z0-9_\-]+", r"brd-customer-***-zone-***:***", cleaned)
    return cleaned


def sanitize_audit_data(data: Any) -> Any:
    """Recursively redacts secrets and sensitive key values from structured data structures."""
    if isinstance(data, dict):
        sanitized_dict: Dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(sensitive in k_lower for sensitive in SENSITIVE_KEY_NAMES):
                sanitized_dict[k] = "***REDACTED***"
            else:
                sanitized_dict[k] = sanitize_audit_data(v)
        return sanitized_dict
    elif isinstance(data, list):
        return [sanitize_audit_data(item) for item in data]
    elif isinstance(data, str):
        return sanitize_string(data)
    else:
        return data
