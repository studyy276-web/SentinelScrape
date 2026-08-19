"""Machine-readable execution and audit models for SentinelScrape pipeline."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ExecutionAudit(BaseModel):
    """Structured, sanitized audit record for an execution request."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    request_id: Optional[str] = None
    url: str = ""
    final_status: str = ""
    is_verified: bool = False
    trust_score: float = 0.0
    state_transitions: List[str] = Field(default_factory=list)
    collection_attempts: List[Dict[str, Any]] = Field(default_factory=list)
    healing_attempts: List[Dict[str, Any]] = Field(default_factory=list)
    escalation_attempts: List[Dict[str, Any]] = Field(default_factory=list)
    failure_signature: Optional[str] = None
    failed_fields: List[str] = Field(default_factory=list)
    validation_summary: Optional[Dict[str, Any]] = None
    budget_summary: Optional[Dict[str, Any]] = None
    timing_ms: Dict[str, float] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
