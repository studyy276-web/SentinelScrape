"""Anomaly detection module for historical baseline comparison."""

from typing import Any, Dict, List, Optional
from app.validation.rules import parse_numeric_value


def extract_price_from_data(data: Any) -> Optional[float]:
    """Extracts numeric price from extracted data if available."""
    if isinstance(data, dict):
        if "price" in data:
            return parse_numeric_value(data["price"])
        if "unit_price" in data:
            return parse_numeric_value(data["unit_price"])
    elif isinstance(data, (int, float)):
        return float(data)
    elif isinstance(data, str):
        return parse_numeric_value(data)
    return None


def extract_item_count_from_data(data: Any) -> Optional[int]:
    """Extracts item or product count from extracted data if available."""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            return len(data["items"])
        if "products" in data and isinstance(data["products"], list):
            return len(data["products"])
        if "item_count" in data:
            val = parse_numeric_value(data["item_count"])
            return int(val) if val is not None else None
        if "count" in data:
            val = parse_numeric_value(data["count"])
            return int(val) if val is not None else None
    return None


class AnomalyDetector:
    """Detects historical anomalies against a known verified baseline."""

    def __init__(
        self,
        price_jump_threshold: float = 0.5,
        count_collapse_threshold: float = 0.5,
        fail_on_anomaly: bool = True,
    ):
        self.price_jump_threshold = price_jump_threshold
        self.count_collapse_threshold = count_collapse_threshold
        self.fail_on_anomaly = fail_on_anomaly

    def check_anomalies(
        self,
        current_data: Any,
        baseline: Optional[Dict[str, Any]],
        current_price: Optional[float] = None,
        current_count: Optional[int] = None,
    ) -> List[str]:
        """Compares current extraction with baseline and returns list of anomaly tags."""
        if not baseline:
            return []

        anomalies: List[str] = []

        # 1. Price jump check
        prev_price = baseline.get("price")
        if prev_price is None and baseline.get("data") is not None:
            prev_price = extract_price_from_data(baseline["data"])

        curr_price = current_price
        if curr_price is None and current_data is not None:
            curr_price = extract_price_from_data(current_data)

        if prev_price is not None and curr_price is not None and prev_price > 0:
            price_diff_ratio = abs(curr_price - prev_price) / prev_price
            if price_diff_ratio > self.price_jump_threshold:
                anomalies.append(f"ANOMALY:PRICE_JUMP (prev={prev_price}, curr={curr_price})")

        # 2. Item count collapse check
        prev_count = baseline.get("item_count")
        if prev_count is None and baseline.get("data") is not None:
            prev_count = extract_item_count_from_data(baseline["data"])

        curr_count = current_count
        if curr_count is None and current_data is not None:
            curr_count = extract_item_count_from_data(current_data)

        if prev_count is not None and curr_count is not None and prev_count > 0:
            if curr_count < prev_count:
                drop_ratio = (prev_count - curr_count) / prev_count
                if drop_ratio > self.count_collapse_threshold:
                    anomalies.append(f"ANOMALY:COUNT_COLLAPSE (prev={prev_count}, curr={curr_count})")

        return anomalies
