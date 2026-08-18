"""Shared data contract for SentinelScrape."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SentinelResponse(BaseModel):
    """Unified response model shared across all roles (Role 1, Role 2, Role 3).
    
    Exact field contract:
    - collector_id
    - url
    - schema
    - extracted_data
    - trust_score
    - status
    - failure_signature
    - failed_fields
    - healing_source
    - compute_tier
    - healing_attempts
    - verification_result
    - cost_ledger
    - ai_answer
    """

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    collector_id: Optional[str] = None
    url: str = ""
    schema: Optional[Dict[str, Any]] = Field(default=None)
    extracted_data: Optional[Any] = None
    trust_score: float = 0.0
    status: str = "pending"
    failure_signature: Optional[str] = None
    failed_fields: List[str] = Field(default_factory=list)
    healing_source: Optional[str] = None
    compute_tier: Optional[str] = None
    healing_attempts: int = 0
    verification_result: Optional[Dict[str, Any]] = None
    cost_ledger: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ai_answer: Optional[str] = None
