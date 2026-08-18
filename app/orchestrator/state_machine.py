"""SentinelScrape Orchestration State Machine implementation."""

import logging
from typing import Optional

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
    ):
        self.collector = collector or StubCollector()
        self.validator = validator or StubValidator()
        self.diagnoser = diagnoser or StubDiagnoser()
        self.healer = healer or StubHealer()
        self.escalator = escalator or StubEscalator()
        self.ai_service = ai_service or StubAIService()

    def step(self, context: OrchestrationContext) -> OrchestrationContext:
        """Executes a single deterministic state transition in the state machine."""
        current_state = context.status

        # 1. START
        if current_state == OrchestratorState.START:
            context.record_state(OrchestratorState.COLLECTING)
            return context

        # 2. COLLECTING
        elif current_state == OrchestratorState.COLLECTING:
            context = self.collector.collect(context)
            context.record_state(OrchestratorState.VALIDATING)
            return context

        # 3. VALIDATING
        elif current_state == OrchestratorState.VALIDATING:
            context = self.validator.validate(context)
            context.record_state(OrchestratorState.TRUST_GATE)
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
            context = self.diagnoser.diagnose(context)
            
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

            context.healing_attempts += 1
            context = self.healer.heal(context)
            # Reset pipeline back to collection/validation
            context.record_state(OrchestratorState.COLLECTING)
            return context

        # 7. ESCALATING
        elif current_state == OrchestratorState.ESCALATING:
            if context.escalation_attempts >= context.max_escalation_attempts:
                # Enforce escalation limit safeguard
                context.record_state(OrchestratorState.BLOCKED)
                return context

            context.escalation_attempts += 1
            context = self.escalator.escalate(context)
            
            if context.metadata.get("escalation_succeeded", True):
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

            context = self.ai_service.generate_answer(context, prompt=context.ai_prompt)
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

        return context
