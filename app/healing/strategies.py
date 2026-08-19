"""Self-healing strategy definitions and DOM remediation utilities."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class HealingStrategy(str, Enum):
    """Enumeration of deterministic self-healing strategies."""

    CSS_SELECTOR_FALLBACK = "CSS_SELECTOR_FALLBACK"    # Try alternative DOM selectors / XPath
    DOM_HEURISTIC_PARSER = "DOM_HEURISTIC_PARSER"      # Semantic heuristics on raw HTML content
    DATA_NORMALIZATION = "DATA_NORMALIZATION"          # Coerce currency symbols, trim strings, format numbers
    DYNAMIC_WAIT_ADJUST = "DYNAMIC_WAIT_ADJUST"        # Adjust browser wait/rendering strategy
    FALLBACK_EXTRACTION = "FALLBACK_EXTRACTION"        # Default fallback extraction strategy


class HealingResult(BaseModel):
    """Outcome of a healing attempt."""

    model_config = ConfigDict(populate_by_name=True)

    strategy: HealingStrategy
    success: bool
    remediated_fields: List[str] = Field(default_factory=list)
    healed_data: Optional[Dict[str, Any]] = None
    adjustments: Dict[str, Any] = Field(default_factory=dict)
    details: str = ""
