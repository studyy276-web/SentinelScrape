"""In-memory AI response cache for SentinelScrape Gemini integration."""

import hashlib
import json
from typing import Any, Dict, Optional


class AICache:
    """Thread-safe-ish in-memory cache for deterministic AI fallbacks.
    
    Generates deterministic fingerprints using SHA-256 over the URL,
    prompt, and schema to ensure accurate cache hits.
    """

    def __init__(self):
        self._cache: Dict[str, str] = {}

    def generate_key(self, url: str, prompt: Optional[str], schema: Optional[Dict[str, Any]]) -> str:
        """Generates a deterministic hash key for the caching payload."""
        payload = {
            "url": url,
            "prompt": prompt or "",
            "schema": schema or {}
        }
        serialized = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """Retrieves a cached answer if it exists."""
        return self._cache.get(key)

    def set(self, key: str, value: str) -> None:
        """Stores an answer in the cache."""
        self._cache[key] = value

    def clear(self) -> None:
        """Clears the cache (useful for testing)."""
        self._cache.clear()
