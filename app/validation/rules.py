"""Validation rules for data types and field values in SentinelScrape."""

import re
from typing import Any, Callable, Dict, Optional, Tuple


def parse_numeric_value(val: Any) -> Optional[float]:
    """Attempts to parse any numeric or string representation into a float."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = re.sub(r"[^\d.-]", "", val.strip())
        if not cleaned or cleaned == "-" or cleaned == ".":
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def validate_string(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is a string."""
    if not isinstance(val, str):
        return False, "INVALID_TYPE"
    return True, None


def validate_non_empty_string(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is a non-empty string."""
    if not isinstance(val, str):
        return False, "INVALID_TYPE"
    if not val.strip():
        return False, "EMPTY_FIELD"
    return True, None


def validate_number(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is a valid number."""
    if val is None or isinstance(val, bool):
        return False, "INVALID_TYPE"
    if isinstance(val, (int, float)):
        return True, None
    if isinstance(val, str):
        if parse_numeric_value(val) is not None:
            return True, None
    return False, "INVALID_TYPE"


def validate_integer(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is an integer."""
    if val is None or isinstance(val, bool):
        return False, "INVALID_TYPE"
    if isinstance(val, int):
        return True, None
    if isinstance(val, str):
        cleaned = val.strip()
        if re.match(r"^-?\d+$", cleaned):
            return True, None
    return False, "INVALID_TYPE"


def validate_boolean(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is a boolean."""
    if isinstance(val, bool):
        return True, None
    if isinstance(val, str) and val.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return True, None
    return False, "INVALID_TYPE"


def validate_price(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value represents a valid positive price."""
    if val is None or isinstance(val, bool):
        return False, "INVALID_TYPE"
    if isinstance(val, (int, float)):
        if val >= 0:
            return True, None
        return False, "INVALID_FIELD"
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return False, "EMPTY_FIELD"
        parsed = parse_numeric_value(cleaned)
        if parsed is not None and parsed >= 0:
            return True, None
        return False, "INVALID_FIELD"
    return False, "INVALID_TYPE"


def validate_rating(val: Any, min_val: float = 0.0, max_val: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Validates that a value represents a valid rating within bounds."""
    if val is None or isinstance(val, bool):
        return False, "INVALID_TYPE"
    parsed = parse_numeric_value(val)
    if parsed is not None:
        if min_val <= parsed <= max_val:
            return True, None
        return False, "INVALID_FIELD"
    return False, "INVALID_TYPE"


def validate_availability(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates stock or item availability representation."""
    if isinstance(val, bool):
        return True, None
    if isinstance(val, str):
        cleaned = val.strip().lower()
        if not cleaned:
            return False, "EMPTY_FIELD"
        valid_statuses = {
            "in stock", "in_stock", "instock",
            "out of stock", "out_of_stock", "outofstock",
            "available", "unavailable",
            "pre-order", "preorder", "backorder", "limited stock",
            "true", "false"
        }
        if cleaned in valid_statuses or len(cleaned) > 0:
            return True, None
    return False, "INVALID_FIELD"


def validate_list(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is a list / array."""
    if isinstance(val, list):
        return True, None
    return False, "INVALID_TYPE"


def validate_dict(val: Any) -> Tuple[bool, Optional[str]]:
    """Validates that a value is a dict / object."""
    if isinstance(val, dict):
        return True, None
    return False, "INVALID_TYPE"


def validate_any(val: Any) -> Tuple[bool, Optional[str]]:
    """Accepts any non-None value."""
    if val is not None:
        return True, None
    return False, "EMPTY_FIELD"


# Rule Registry mapping type strings to validator functions
RULE_REGISTRY: Dict[str, Callable[[Any], Tuple[bool, Optional[str]]]] = {
    "string": validate_string,
    "str": validate_string,
    "non-empty string": validate_non_empty_string,
    "non_empty_string": validate_non_empty_string,
    "nonempty_string": validate_non_empty_string,
    "number": validate_number,
    "float": validate_number,
    "int": validate_integer,
    "integer": validate_integer,
    "bool": validate_boolean,
    "boolean": validate_boolean,
    "price": validate_price,
    "rating": validate_rating,
    "availability": validate_availability,
    "list": validate_list,
    "array": validate_list,
    "dict": validate_dict,
    "object": validate_dict,
    "any": validate_any,
}


def get_rule_validator(rule_name: str) -> Callable[[Any], Tuple[bool, Optional[str]]]:
    """Retrieves a validator function from the registry, defaulting to non_empty_string."""
    normalized = rule_name.strip().lower()
    return RULE_REGISTRY.get(normalized, validate_non_empty_string)
