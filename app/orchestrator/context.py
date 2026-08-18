"""Orchestration context model tracking workflow state and metadata."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.models.response import SentinelResponse
from app.orchestrator.states import OrchestratorState


class OrchestrationContext(BaseModel):
    """Context tracking the state machine execution.
    
    Includes all fields required by SentinelResponse and orchestrator internal metrics.
    """

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=(), arbitrary_types_allowed=True)

    collector_id: Optional[str] = None
    url: str = ""
    schema: Optional[Dict[str, Any]] = Field(default=None)
    extracted_data: Optional[Any] = None
    trust_score: float = 0.0
    status: OrchestratorState = OrchestratorState.START
    failure_signature: Optional[str] = None
    failed_fields: List[str] = Field(default_factory=list)
    healing_source: Optional[str] = None
    compute_tier: Optional[str] = "standard"
    healing_attempts: int = 0
    escalation_attempts: int = 0
    max_healing_attempts: int = 2
    max_escalation_attempts: int = 1
    verification_result: Optional[Dict[str, Any]] = None
    cost_ledger: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ai_answer: Optional[str] = None
    ai_prompt: Optional[str] = None
    is_healable: bool = True
    state_history: List[OrchestratorState] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def record_state(self, new_state: OrchestratorState) -> None:
        """Transitions current state and records it in state_history."""
        self.status = new_state
        self.state_history.append(new_state)

    def is_verified(self) -> bool:
        """Checks if the data has passed verification."""
        if self.verification_result and self.verification_result.get("passed", False):
            return True
        if self.trust_score >= 0.85 and not self.failed_fields:
            return True
        return False

    def to_response(self) -> SentinelResponse:
        """Serializes the context into the immutable SentinelResponse data contract."""
        return SentinelResponse(
            collector_id=self.collector_id,
            url=self.url,
            schema=self.schema,
            extracted_data=self.extracted_data,
            trust_score=self.trust_score,
            status=self.status.value,
            failure_signature=self.failure_signature,
            failed_fields=self.failed_fields,
            healing_source=self.healing_source,
            compute_tier=self.compute_tier,
            healing_attempts=self.healing_attempts,
            verification_result=self.verification_result,
            cost_ledger=self.cost_ledger or {},
            ai_answer=self.ai_answer,
        )
