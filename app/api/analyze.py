"""Analysis API endpoint integrating orchestrator state machine and SentinelValidator."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException

from app.diagnosis.diagnoser import SentinelDiagnoser
from app.healing.escalator import SentinelEscalator
from app.healing.healer import SentinelHealer
from app.integrations.brightdata.collector import BrightDataCollector
from app.ledger.budget_guard import BudgetGuard
from app.models.request import AnalyzeRequest
from app.models.response import SentinelResponse
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Analysis"])

from app.integrations.gemini.service import GoogleGeminiService

# Persistent pipeline components for the API
_shared_collector = BrightDataCollector()
_shared_validator = SentinelValidator()
_shared_diagnoser = SentinelDiagnoser()
_shared_healer = SentinelHealer()
_shared_escalator = SentinelEscalator()
_shared_ai_service = GoogleGeminiService()
_shared_budget_guard = BudgetGuard()


def get_orchestrator() -> SentinelOrchestrator:
    """Dependency provider returning an orchestrator wired with BrightDataCollector, SentinelValidator, SentinelDiagnoser, SentinelHealer, SentinelEscalator, GoogleGeminiService, and BudgetGuard."""
    return SentinelOrchestrator(
        collector=_shared_collector,
        validator=_shared_validator,
        diagnoser=_shared_diagnoser,
        healer=_shared_healer,
        escalator=_shared_escalator,
        ai_service=_shared_ai_service,
        budget_guard=_shared_budget_guard,
    )


@router.post("/analyze", response_model=SentinelResponse)
async def analyze_data(
    request: AnalyzeRequest,
    orchestrator: SentinelOrchestrator = Depends(get_orchestrator),
) -> SentinelResponse:
    """Executes the SentinelScrape orchestration workflow:
    
    1. Ingests request parameters (url, schema, prompt, collector_id, etc.).
    2. Coordinates deterministic state machine (COLLECTING -> VALIDATING -> TRUST_GATE -> VERIFIED -> AI_READY).
    3. Evaluates schema compliance, trust scores, baseline metrics, and anomaly checks via SentinelValidator.
    4. Serializes outcome into the unified SentinelResponse contract.
    """
    if not request.url or not request.url.strip():
        raise HTTPException(status_code=422, detail="URL field must not be empty.")

    context = OrchestrationContext(
        url=request.url,
        schema=request.schema,
        ai_prompt=request.prompt,
        collector_id=request.collector_id,
        metadata=request.metadata or {},
        compute_tier=request.compute_tier or "standard",
    )

    try:
        final_context = orchestrator.run(context)
    except Exception as e:
        logger.exception("Orchestration pipeline execution error: %s", e)
        context.metadata["error"] = str(e)
        context.record_state(OrchestratorState.FAILED)
        return context.to_response()

    return final_context.to_response()
