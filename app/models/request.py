"""Request models for SentinelScrape API."""

from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class AnalyzeRequest(BaseModel):
    """Payload schema for POST /analyze requests."""

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    url: str
    schema: Optional[Dict[str, Any]] = Field(default=None)
    prompt: Optional[str] = Field(default=None)
    collector_id: Optional[str] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    compute_tier: Optional[str] = "standard"
