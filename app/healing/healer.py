"""Deterministic self-healing engine implementing orchestrator Healer protocol."""

import logging
import re
from typing import Any, Dict, List, Optional

from app.healing.strategies import HealingResult, HealingStrategy
from app.orchestrator.context import OrchestrationContext

logger = logging.getLogger(__name__)


class SentinelHealer:
    """Deterministic self-healing engine for remediating selector and data extraction failures."""

    def select_strategy(
        self,
        failure_signature: Optional[str] = None,
        failed_fields: Optional[List[str]] = None,
        attempt: int = 1,
    ) -> HealingStrategy:
        """Determines the optimal healing strategy based on failure signatures and attempt count."""
        sig = (failure_signature or "").upper()

        if "TYPE_MISMATCH" in sig or "INVALID_PRICE" in sig or "EMPTY_FIELD" in sig or "INVALID_VALUE" in sig:
            return HealingStrategy.DATA_NORMALIZATION

        if "MISSING_FIELD" in sig or "SELECTOR" in sig or "CSS" in sig or "DOM" in sig:
            if attempt == 1:
                return HealingStrategy.CSS_SELECTOR_FALLBACK
            return HealingStrategy.DOM_HEURISTIC_PARSER

        if "DYNAMIC" in sig or "WAIT" in sig:
            return HealingStrategy.DYNAMIC_WAIT_ADJUST

        # Default progression by attempt
        if attempt == 1:
            return HealingStrategy.CSS_SELECTOR_FALLBACK
        return HealingStrategy.DOM_HEURISTIC_PARSER

    def normalize_value(self, field_name: str, value: Any, rule: Optional[str] = None) -> Any:
        """Applies normalization heuristics to coerce malformed data into valid types."""
        if value is None:
            return value

        str_val = str(value).strip()

        # Handle price string coercion (e.g., "$1,299.99 USD" -> 1299.99)
        if rule == "price" or "price" in field_name.lower():
            cleaned = re.sub(r"[^\d.]", "", str_val)
            try:
                return float(cleaned)
            except (ValueError, TypeError):
                return value

        # Handle rating (e.g., "4.8 / 5.0" -> 4.8)
        if rule == "rating" or "rating" in field_name.lower():
            match = re.search(r"(\d+(?:\.\d+)?)", str_val)
            if match:
                try:
                    return float(match.group(1))
                except (ValueError, TypeError):
                    pass

        # Handle numeric string coercion
        if rule in ("number", "integer") or any(k in field_name.lower() for k in ("count", "qty")):
            cleaned = re.sub(r"[^\d.]", "", str_val)
            try:
                return float(cleaned) if "." in cleaned else int(cleaned)
            except (ValueError, TypeError):
                return value

        # Handle whitespace trimming for string fields
        return str_val

    def heal(self, context: OrchestrationContext) -> OrchestrationContext:
        """Executes deterministic self-healing on the context."""
        logger.info(
            "Executing self-healing attempt %d for url=%s",
            context.healing_attempts,
            context.url,
        )

        strategy = self.select_strategy(
            failure_signature=context.failure_signature,
            failed_fields=context.failed_fields,
            attempt=context.healing_attempts,
        )

        remediated_fields: List[str] = list(context.failed_fields or [])
        adjustments: Dict[str, Any] = {}
        healed_data: Optional[Dict[str, Any]] = None

        if strategy == HealingStrategy.CSS_SELECTOR_FALLBACK:
            adjustments["selector_fallbacks"] = {
                field: [f"[data-testid*='{field}']", f".{field}", f"#{field}", f"[aria-label*='{field}']"]
                for field in remediated_fields
            }
            adjustments["wait_for_selector"] = True

        elif strategy == HealingStrategy.DOM_HEURISTIC_PARSER:
            adjustments["use_heuristic_parser"] = True
            adjustments["extract_all_text"] = True

        elif strategy == HealingStrategy.DATA_NORMALIZATION:
            adjustments["coerce_types"] = True
            adjustments["strip_formatting"] = True
            if context.extracted_data and isinstance(context.extracted_data, dict):
                healed_data = dict(context.extracted_data)
                schema = context.schema or {}
                for f, val in healed_data.items():
                    rule = schema.get(f, {}).get("type") if isinstance(schema.get(f), dict) else schema.get(f)
                    healed_data[f] = self.normalize_value(f, val, rule=rule)

        context.healing_source = strategy.value
        context.metadata["healed"] = True
        context.metadata["healing_details"] = {
            "strategy": strategy.value,
            "attempt": context.healing_attempts,
            "remediated_fields": remediated_fields,
            "adjustments": adjustments,
        }
        if adjustments:
            context.metadata["healing_adjustments"] = adjustments

        # If data normalization successfully produced cleaned data, populate it
        if healed_data is not None:
            context.extracted_data = healed_data

        return context
