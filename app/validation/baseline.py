import threading
import time
from typing import Any, Dict, Optional


class BaselineStore:
    """In-memory deterministic baseline storage for verified extraction snapshots."""

    def __init__(self):
        self._baselines: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get_baseline(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves historical baseline for a given URL or identifier."""
        with self._lock:
            record = self._baselines.get(key)
            if record is not None:
                return dict(record)
            return None

    def has_baseline(self, key: str) -> bool:
        """Returns True if a baseline exists for the key."""
        with self._lock:
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
        with self._lock:
            self._baselines[key] = baseline_record
        return baseline_record

    def clear(self) -> None:
        """Clears all stored baselines."""
        with self._lock:
            self._baselines.clear()
