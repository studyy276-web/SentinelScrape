"""ExecutionAuditor constructing machine-readable audit records from workflow execution contexts."""

import logging
from typing import Any, Dict, List, Optional

from app.audit.models import ExecutionAudit
from app.audit.sanitizer import sanitize_audit_data, sanitize_url
from app.orchestrator.context import OrchestrationContext

logger = logging.getLogger(__name__)


class ExecutionAuditor:
    """Builds structured, sanitized audit records from orchestration contexts."""

    @classmethod
    def build_audit_record(cls, context: OrchestrationContext) -> ExecutionAudit:
        """Constructs an ExecutionAudit model from the final context."""
        state_transitions: List[str] = [
            s.value if hasattr(s, "value") else str(s)
            for s in context.state_history
        ]

        # Validation summary extraction
        val_summary: Optional[Dict[str, Any]] = None
        if context.verification_result:
            val_summary = {
                "passed": context.verification_result.get("passed", False),
                "trust_score": context.verification_result.get("trust_score", 0.0),
                "fields_expected": context.verification_result.get("fields_expected", 0),
                "fields_present": context.verification_result.get("fields_present", 0),
                "fields_valid": context.verification_result.get("fields_valid", 0),
                "failed_fields": context.verification_result.get("failed_fields", []),
                "is_baseline": context.verification_result.get("is_baseline", False),
                "anomaly_count": len(context.verification_result.get("anomalies", [])),
            }

        # Budget summary extraction
        budget_summary: Optional[Dict[str, Any]] = None
        if context.cost_ledger:
            budget_summary = {
                "total_cost_usd": context.cost_ledger.get("total_cost_usd", 0.0),
                "total_tokens": context.cost_ledger.get("total_tokens", 0),
                "api_calls": context.cost_ledger.get("api_calls", 0),
                "budget_exceeded": context.metadata.get("budget_exceeded", False),
                "breakdown": context.cost_ledger.get("breakdown", {}),
            }

        # Timing extraction
        timing_ms: Dict[str, float] = {}
        if "brightdata" in context.metadata and isinstance(context.metadata["brightdata"], dict):
            timing_ms["collector_response_ms"] = float(
                context.metadata["brightdata"].get("response_time_ms", 0.0)
            )

        # Context metadata (excluding audit_record to avoid recursion)
        sanitized_meta: Dict[str, Any] = {}
        for k, v in context.metadata.items():
            if k != "audit_record":
                sanitized_meta[k] = sanitize_audit_data(v)

        return ExecutionAudit(
            request_id=context.collector_id,
            url=sanitize_url(context.url),
            final_status=context.status.value if hasattr(context.status, "value") else str(context.status),
            is_verified=context.is_verified(),
            trust_score=context.trust_score,
            state_transitions=state_transitions,
            collection_attempts=sanitize_audit_data(context.metadata.get("attempt_history", [])),
            healing_attempts=sanitize_audit_data(context.metadata.get("healing_history", [])),
            escalation_attempts=sanitize_audit_data(context.metadata.get("escalation_history", [])),
            failure_signature=context.failure_signature,
            failed_fields=list(context.failed_fields or []),
            validation_summary=val_summary,
            budget_summary=budget_summary,
            timing_ms=timing_ms,
            metadata=sanitized_meta,
        )
