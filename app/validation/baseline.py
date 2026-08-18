"""Baseline management for tracking historical verified extraction data."""

from typing import Any, Dict, Optional
import time


class BaselineStore:
    """In-memory deterministic baseline storage for verified extraction snapshots."""

    def __init__(self):
        self._baselines: Dict[str, Dict[str, Any]] = {}

    def get_baseline(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves historical baseline for a given URL or identifier."""
        return self._baselines.get(key)

    def has_baseline(self, key: str) -> bool:
        """Returns True if a baseline exists for the key."""
        return key in self._baselines

    def save_baseline(
        self,
        key: str,
        data: Any,
        price: Optional[float] = None,
        item_count: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Saves or updates a verified baseline entry."""
        baseline_record = {
            "key": key,
            "data": data,
            "price": price,
            "item_count": item_count,
            "created_at": time.time(),
            "metadata": metadata or {},
        }
        self._baselines[key] = baseline_record
        return baseline_record

    def clear(self) -> None:
        """Clears all stored baselines."""
        self._baselines.clear()
