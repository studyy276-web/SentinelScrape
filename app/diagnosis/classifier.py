"""Deterministic failure classification for SentinelScrape failure signatures."""

from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field


class FailureCategory(str, Enum):
    """Categorization of extraction and validation failures."""

    SCHEMA_EXTRACTION = "SCHEMA_EXTRACTION"      # Healable (missing selectors, DOM extraction issues)
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"      # Healable (type mismatch, empty fields, bad formatting)
    ANOMALY = "ANOMALY"                          # Non-healable (historical price/count deviation)
    INFRASTRUCTURE = "INFRASTRUCTURE"            # Non-healable (CAPTCHA, rate limit, blocking)
    STRUCTURAL_CLIENT = "STRUCTURAL_CLIENT"      # Non-healable (empty/invalid schema, client payload errors)
    UNKNOWN = "UNKNOWN"                          # Non-healable by default for safety


# Deterministic set of healable categories
HEALABLE_CATEGORIES: Set[FailureCategory] = {
    FailureCategory.SCHEMA_EXTRACTION,
    FailureCategory.SCHEMA_VALIDATION,
}

# Prefix and tag mapping rules
_INFRASTRUCTURE_PREFIXES = (
    "CAPTCHA",
    "CLOUDFLARE",
    "BOT_DETECT",
    "HARD_BOT",
    "IP_RATE_LIMIT",
    "RATE_LIMIT",
    "PROXY",
    "BLOCKED",
    "HTTP_403",
    "HTTP_429",
    "HTTP_5",
    "TIMEOUT",
    "NETWORK",
    "CONNECTION",
    "BUDGET",
)

_ANOMALY_PREFIXES = (
    "ANOMALY",
    "PRICE_JUMP",
    "COUNT_COLLAPSE",
    "HISTORICAL",
)

_STRUCTURAL_CLIENT_PREFIXES = (
    "NO_EXPECTED_FIELDS",
    "EMPTY_SCHEMA",
    "MALFORMED_SCHEMA",
    "CLIENT_ERROR",
    "AUTH_FAILURE",
    "CORRUPT",
)

_SCHEMA_EXTRACTION_PREFIXES = (
    "MISSING_FIELD",
    "SELECTOR",
    "CSS",
    "XPATH",
    "DOM",
    "DYNAMIC_CONTENT",
    "EXTRACTION",
)

_SCHEMA_VALIDATION_PREFIXES = (
    "EMPTY_FIELD",
    "INVALID_FIELD",
    "INVALID_TYPE",
    "INVALID_VALUE",
    "VALIDATION",
    "TYPE_MISMATCH",
    "PARSE_ERROR",
)


class FailureClassification(BaseModel):
    """Result of failure signature classification."""

    model_config = ConfigDict(populate_by_name=True)

    category: FailureCategory
    is_healable: bool
    primary_signature: str
    all_signatures: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class FailureClassifier:
    """Deterministic, side-effect-free failure signature classifier."""

    def classify_tag(self, tag: str) -> FailureCategory:
        """Classifies an individual failure tag string."""
        clean_tag = tag.strip().upper()
        if not clean_tag:
            return FailureCategory.UNKNOWN

        # 1. Structural / Client-level invalid schema
        for prefix in _STRUCTURAL_CLIENT_PREFIXES:
            if clean_tag.startswith(prefix) or prefix in clean_tag:
                return FailureCategory.STRUCTURAL_CLIENT

        # 2. Historical Anomalies
        for prefix in _ANOMALY_PREFIXES:
            if clean_tag.startswith(prefix) or prefix in clean_tag:
                return FailureCategory.ANOMALY

        # 3. Infrastructure & Blocking
        for prefix in _INFRASTRUCTURE_PREFIXES:
            if clean_tag.startswith(prefix) or prefix in clean_tag:
                return FailureCategory.INFRASTRUCTURE

        # 4. Schema Extraction (Selectors / missing fields)
        for prefix in _SCHEMA_EXTRACTION_PREFIXES:
            if clean_tag.startswith(prefix) or prefix in clean_tag:
                return FailureCategory.SCHEMA_EXTRACTION

        # 5. Schema Validation (Invalid type/value/empty)
        for prefix in _SCHEMA_VALIDATION_PREFIXES:
            if clean_tag.startswith(prefix) or prefix in clean_tag:
                return FailureCategory.SCHEMA_VALIDATION

        return FailureCategory.UNKNOWN

    def classify(
        self,
        failure_signature: Optional[str] = None,
        failed_fields: Optional[List[str]] = None,
        verification_result: Optional[Dict[str, Any]] = None,
    ) -> FailureClassification:
        """Classifies overall failure state deterministically.
        
        If multiple failure signatures are present:
        - Non-healable failures (ANOMALY, INFRASTRUCTURE, STRUCTURAL_CLIENT, UNKNOWN) take precedence.
        - The primary category is chosen deterministically by highest severity.
        """
        # Parse tags from failure_signature (comma-separated or single)
        raw_tags: List[str] = []
        if failure_signature and failure_signature.strip():
            raw_tags = [t.strip() for t in failure_signature.split(",") if t.strip()]

        # If no explicit signature tags, derive from verification_result or failed_fields
        if not raw_tags:
            if verification_result:
                if verification_result.get("anomalies"):
                    raw_tags.extend(verification_result["anomalies"])
                if verification_result.get("failure_signature"):
                    raw_tags.extend([
                        t.strip()
                        for t in str(verification_result["failure_signature"]).split(",")
                        if t.strip()
                    ])
            if not raw_tags and failed_fields:
                raw_tags.extend([f"MISSING_FIELD:{f}" for f in failed_fields if f])

        if not raw_tags:
            # No failure indicated
            return FailureClassification(
                category=FailureCategory.UNKNOWN,
                is_healable=False,
                primary_signature="UNKNOWN_FAILURE",
                all_signatures=[],
                details={"reason": "No failure signatures or failed fields provided."},
            )

        # Categorize all individual tags
        tag_categories: List[Tuple[str, FailureCategory]] = [
            (tag, self.classify_tag(tag)) for tag in raw_tags
        ]

        # Deterministic severity precedence order (Highest to Lowest):
        # 1. STRUCTURAL_CLIENT (Non-healable)
        # 2. INFRASTRUCTURE (Non-healable)
        # 3. ANOMALY (Non-healable)
        # 4. UNKNOWN (Non-healable)
        # 5. SCHEMA_EXTRACTION (Healable)
        # 6. SCHEMA_VALIDATION (Healable)
        category_priority = {
            FailureCategory.STRUCTURAL_CLIENT: 1,
            FailureCategory.INFRASTRUCTURE: 2,
            FailureCategory.ANOMALY: 3,
            FailureCategory.UNKNOWN: 4,
            FailureCategory.SCHEMA_EXTRACTION: 5,
            FailureCategory.SCHEMA_VALIDATION: 6,
        }

        # Sort tags by category priority
        sorted_by_priority = sorted(
            tag_categories,
            key=lambda item: (category_priority.get(item[1], 99), item[0]),
        )

        primary_tag, primary_category = sorted_by_priority[0]

        # Overall healability: all tags must be in HEALABLE_CATEGORIES
        is_healable = all(cat in HEALABLE_CATEGORIES for _, cat in tag_categories)

        return FailureClassification(
            category=primary_category,
            is_healable=is_healable,
            primary_signature=", ".join(raw_tags),
            all_signatures=raw_tags,
            details={
                "primary_category": primary_category.value,
                "categorized_tags": {tag: cat.value for tag, cat in tag_categories},
                "is_healable": is_healable,
            },
        )
