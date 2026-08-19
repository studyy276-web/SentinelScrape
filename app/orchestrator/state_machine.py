"""SentinelScrape Orchestration State Machine implementation."""

import logging
from typing import Optional

from app.audit.auditor import ExecutionAuditor
from app.ledger.budget_guard import BudgetGuard
from app.ledger.cost_ledger import CostLedger
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import (
    AIService,
    Collector,
    Diagnoser,
    Escalator,
    Healer,
    StubAIService,
    StubCollector,
    StubDiagnoser,
    StubEscalator,
    StubHealer,
    StubValidator,
    Validator,
)
from app.orchestrator.states import OrchestratorState

logger = logging.getLogger(__name__)


class SentinelOrchestrator:
    """Deterministic orchestration state machine for SentinelScrape.
    
    Coordinates the pipeline:
    START -> COLLECTING -> VALIDATING -> TRUST_GATE -> VERIFIED -> AI_READY
    
    With self-healing loop:
    TRUST_GATE -> DIAGNOSING -> HEALING -> COLLECTING -> VALIDATING -> TRUST_GATE
    
    With escalation:
    TRUST_GATE -> ESCALATING -> COLLECTING -> ...
    
    With terminal blocking:
    -> BLOCKED
    """

    def __init__(
        self,
        collector: Optional[Collector] = None,
        validator: Optional[Validator] = None,
        diagnoser: Optional[Diagnoser] = None,
        healer: Optional[Healer] = None,
        escalator: Optional[Escalator] = None,
        ai_service: Optional[AIService] = None,
        budget_guard: Optional[BudgetGuard] = None,
        enable_cost_tracking: bool = False,
    ):
        self.collector = collector or StubCollector()
        self.validator = validator or StubValidator()
        self.diagnoser = diagnoser or StubDiagnoser()
        self.healer = healer or StubHealer()
        self.escalator = escalator or StubEscalator()
        self.ai_service = ai_service or StubAIService()
        self.budget_guard = budget_guard
        self.enable_cost_tracking = enable_cost_tracking or (budget_guard is not None)

    def step(self, context: OrchestrationContext) -> OrchestrationContext:
        """Executes a single deterministic state transition in the state machine."""
        current_state = context.status

        # 1. START
        if current_state == OrchestratorState.START:
            context.record_state(OrchestratorState.COLLECTING)
            return context

        # 2. COLLECTING
        elif current_state == OrchestratorState.COLLECTING:
            if self.budget_guard:
                ledger = CostLedger(initial_data=context.cost_ledger)
                tier_cost = ledger.get_tier_rate(context.compute_tier)
                if not self.budget_guard.enforce(context, ledger=ledger, proposed_cost_usd=tier_cost):
                    return context

            # Record collection attempt in structured history
            if "attempt_history" not in context.metadata:
                context.metadata["attempt_history"] = []
            context.metadata["attempt_history"].append({
                "stage": "COLLECTING",
                "tier": context.compute_tier or "standard",
                "healing_attempt": context.healing_attempts,
                "escalation_attempt": context.escalation_attempts,
            })

            try:
                context = self.collector.collect(context)
                if self.enable_cost_tracking:
                    ledger = CostLedger(initial_data=context.cost_ledger)
                    ledger.record_collection(tier=context.compute_tier or "standard")
                    context.cost_ledger = ledger.to_dict()
                context.record_state(OrchestratorState.VALIDATING)
            except Exception as e:
                logger.exception("Collector failed: %s", e)
                context.metadata["error"] = str(e)
                context.record_state(OrchestratorState.FAILED)
            return context

        # 3. VALIDATING
        elif current_state == OrchestratorState.VALIDATING:
            try:
                context = self.validator.validate(context)
                context.record_state(OrchestratorState.TRUST_GATE)
            except Exception as e:
                logger.exception("Validator failed: %s", e)
                context.metadata["error"] = str(e)
                context.record_state(OrchestratorState.FAILED)
            return context

        # 4. TRUST_GATE
        elif current_state == OrchestratorState.TRUST_GATE:
            if context.is_verified():
                context.record_state(OrchestratorState.VERIFIED)
            else:
                # Validation failed: evaluate healing limits
                if context.healing_attempts < context.max_healing_attempts:
                    context.record_state(OrchestratorState.DIAGNOSING)
                elif context.escalation_attempts < context.max_escalation_attempts:
                    context.record_state(OrchestratorState.ESCALATING)
                else:
                    context.record_state(OrchestratorState.BLOCKED)
            return context

        # 5. DIAGNOSING
        elif current_state == OrchestratorState.DIAGNOSING:
            try:
                context = self.diagnoser.diagnose(context)
            except Exception as e:
                logger.exception("Diagnoser failed: %s", e)
                context.metadata["error"] = str(e)
                context.record_state(OrchestratorState.FAILED)
                return context
            
            # Non-healable failures immediately route to escalation or blocked
            if not context.is_healable:
                if context.escalation_attempts < context.max_escalation_attempts:
                    context.record_state(OrchestratorState.ESCALATING)
                else:
                    context.record_state(OrchestratorState.BLOCKED)
            elif context.healing_attempts < context.max_healing_attempts:
                context.record_state(OrchestratorState.HEALING)
            elif context.escalation_attempts < context.max_escalation_attempts:
                context.record_state(OrchestratorState.ESCALATING)
            else:
                context.record_state(OrchestratorState.BLOCKED)
            return context

        # 6. HEALING
        elif current_state == OrchestratorState.HEALING:
            if context.healing_attempts >= context.max_healing_attempts:
                # Enforce healing limit safeguard
                if context.escalation_attempts < context.max_escalation_attempts:
                    context.record_state(OrchestratorState.ESCALATING)
                else:
                    context.record_state(OrchestratorState.BLOCKED)
                return context

            if self.budget_guard:
                ledger = CostLedger(initial_data=context.cost_ledger)
                if not self.budget_guard.enforce(context, ledger=ledger, proposed_cost_usd=0.002):
                    return context

            context.healing_attempts += 1
            try:
                context = self.healer.heal(context)
                if self.enable_cost_tracking:
                    ledger = CostLedger(initial_data=context.cost_ledger)
                    ledger.record_healing(source=context.healing_source or "CSS_SELECTOR_FALLBACK")
                    context.cost_ledger = ledger.to_dict()

                # Record healing event in structured history
                if "healing_history" not in context.metadata:
                    context.metadata["healing_history"] = []
                context.metadata["healing_history"].append({
                    "attempt": context.healing_attempts,
                    "strategy": context.healing_source,
                    "failed_fields": list(context.failed_fields or []),
                })

                # Clear stale extraction/validation state before fresh collection
                context.reset_for_new_collection()
                # Reset pipeline back to collection/validation
                context.record_state(OrchestratorState.COLLECTING)
            except Exception as e:
                logger.exception("Healer failed: %s", e)
                context.metadata["error"] = str(e)
                context.record_state(OrchestratorState.FAILED)
            return context

        # 7. ESCALATING
        elif current_state == OrchestratorState.ESCALATING:
            if context.escalation_attempts >= context.max_escalation_attempts:
                # Enforce escalation limit safeguard
                context.record_state(OrchestratorState.BLOCKED)
                return context

            if self.budget_guard:
                ledger = CostLedger(initial_data=context.cost_ledger)
                if not self.budget_guard.enforce(context, ledger=ledger, proposed_cost_usd=0.015):
                    return context

            context.escalation_attempts += 1
            try:
                context = self.escalator.escalate(context)
                if self.enable_cost_tracking:
                    ledger = CostLedger(initial_data=context.cost_ledger)
                    ledger.record_escalation(from_tier="standard", to_tier=context.compute_tier or "unblocker_browser")
                    context.cost_ledger = ledger.to_dict()

                # Record escalation event in structured history
                if "escalation_history" not in context.metadata:
                    context.metadata["escalation_history"] = []
                context.metadata["escalation_history"].append({
                    "attempt": context.escalation_attempts,
                    "tier": context.compute_tier,
                    "reason": context.failure_signature,
                })
            except Exception as e:
                logger.exception("Escalator failed: %s", e)
                context.metadata["error"] = str(e)
                context.record_state(OrchestratorState.FAILED)
                return context
            
            if context.metadata.get("escalation_succeeded") is True:
                # Clear stale extraction/validation state before fresh collection
                context.reset_for_new_collection()
                # Reset pipeline back to collection with escalated tier
                context.record_state(OrchestratorState.COLLECTING)
            else:
                context.record_state(OrchestratorState.BLOCKED)
            return context

        # 8. VERIFIED
        elif current_state == OrchestratorState.VERIFIED:
            # Advance to AI_READY
            context.record_state(OrchestratorState.AI_READY)
            return context

        # 9. AI_READY
        elif current_state == OrchestratorState.AI_READY:
            # Strict safety check: Never process unverified data
            if not context.is_verified():
                context.record_state(OrchestratorState.FAILED)
                raise RuntimeError("Security violation: Attempted AI generation on unverified data.")

            if self.budget_guard:
                ledger = CostLedger(initial_data=context.cost_ledger)
                if not self.budget_guard.enforce(context, ledger=ledger, proposed_cost_usd=0.0005, proposed_tokens=100):
                    return context

            try:
                context = self.ai_service.generate_answer(context, prompt=context.ai_prompt)
                if self.enable_cost_tracking:
                    ledger = CostLedger(initial_data=context.cost_ledger)
                    prompt_len = max(10, len(context.ai_prompt or "") // 4)
                    completion_len = max(10, len(context.ai_answer or "") // 4)
                    ledger.record_ai_generation(prompt_tokens=prompt_len, completion_tokens=completion_len)
                    context.cost_ledger = ledger.to_dict()
            except Exception as e:
                logger.exception("AI service failed: %s", e)
                context.metadata["error"] = str(e)
                context.record_state(OrchestratorState.FAILED)
            return context

        # 10. Terminal States: BLOCKED / FAILED
        elif current_state in (OrchestratorState.BLOCKED, OrchestratorState.FAILED):
            return context

        else:
            context.record_state(OrchestratorState.FAILED)
            return context

    def run(self, context: OrchestrationContext, max_steps: int = 50) -> OrchestrationContext:
        """Runs the state machine from current state until a terminal state or max_steps is reached."""
        if not context.state_history:
            context.state_history.append(context.status)

        steps = 0
        terminal_states = {
            OrchestratorState.AI_READY,
            OrchestratorState.BLOCKED,
            OrchestratorState.FAILED,
        }

        # If already at AI_READY and ai_answer is not yet generated, step one more time
        while steps < max_steps:
            if context.status in terminal_states:
                # If at AI_READY, execute AI service once if answer not generated yet
                if context.status == OrchestratorState.AI_READY and context.ai_answer is None:
                    context = self.step(context)
                break

            context = self.step(context)
            steps += 1

        try:
            audit = ExecutionAuditor.build_audit_record(context)
            context.metadata["audit_record"] = audit.model_dump()
        except Exception as e:
            logger.warning("Failed to construct audit record: %s", e)

        return context
