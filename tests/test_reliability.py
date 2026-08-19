"""Operational reliability, loop-termination, and attempt metadata tests for SentinelScrape."""

from unittest.mock import MagicMock
import pytest

from app.diagnosis.diagnoser import SentinelDiagnoser
from app.healing.escalator import SentinelEscalator
from app.healing.healer import SentinelHealer
from app.integrations.brightdata.client import BrightDataClient, BrightDataResponse
from app.integrations.brightdata.collector import BrightDataCollector
from app.ledger.budget_guard import BudgetConfig, BudgetGuard
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import (
    StubAIService,
    StubCollector,
    StubDiagnoser,
    StubEscalator,
    StubHealer,
    StubValidator,
)
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator


class TestOperationalReliability:
    """Test suite verifying operational safety, loop boundaries, budget limits, and metadata tracking."""

    def test_healing_attempt_limits_enforced_strictly(self):
        """Verify healing loop terminates deterministically when max_healing_attempts is reached."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=StubDiagnoser(is_healable=True),
            healer=StubHealer(success_on_attempt=999),  # Never succeeds
            escalator=StubEscalator(succeed=False),
        )

        ctx = OrchestrationContext(
            url="https://example.com/heal-limit",
            max_healing_attempts=2,
            max_escalation_attempts=1,
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert result.healing_attempts == 2
        assert result.escalation_attempts == 1

    def test_escalation_attempt_limits_enforced_strictly(self):
        """Verify escalation terminates deterministically at BLOCKED when max_escalation_attempts exhausted."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=StubDiagnoser(is_healable=False),  # Direct escalation
            escalator=StubEscalator(succeed=False),       # Escalation fails
        )

        ctx = OrchestrationContext(
            url="https://example.com/esc-limit",
            max_escalation_attempts=1,
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert result.escalation_attempts == 1

    def test_retry_loop_termination_safeguard(self):
        """Verify state machine never loops infinitely and terminates within max_steps."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=StubDiagnoser(is_healable=True),
            healer=StubHealer(success_on_attempt=100),
            escalator=StubEscalator(succeed=False),
        )

        ctx = OrchestrationContext(url="https://example.com/loop-check")
        result = orchestrator.run(ctx, max_steps=30)

        assert result.status in (OrchestratorState.BLOCKED, OrchestratorState.FAILED)
        assert len(result.state_history) < 30

    def test_successful_recovery_after_one_healing_attempt(self):
        """Verify successful recovery on attempt 1 updates state and proceeds to AI_READY."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            # Attempt 0: Missing price
            BrightDataResponse(
                success=True,
                status_code=200,
                data={"title": "Studio Headphones"},
                tier_used="scraping_browser",
            ),
            # Attempt 1: Recovered complete data
            BrightDataResponse(
                success=True,
                status_code=200,
                data={"title": "Studio Headphones", "price": 299.99},
                tier_used="scraping_browser",
            ),
        ]

        collector = BrightDataCollector(client=mock_client)
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            ai_service=StubAIService(),
            enable_cost_tracking=True,
        )

        ctx = OrchestrationContext(
            url="https://example.com/headphones",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 1
        assert result.extracted_data == {"title": "Studio Headphones", "price": 299.99}

    def test_exhausted_healing_transitions_to_escalation(self):
        """Verify when healing attempts are exhausted, state machine transitions to escalation."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            # Attempt 0: missing data
            BrightDataResponse(success=True, status_code=200, data={"item": "Unknown"}),
            # Attempt 1: missing data
            BrightDataResponse(success=True, status_code=200, data={"item": "Unknown"}),
            # Attempt 2: missing data (exhausts healing)
            BrightDataResponse(success=True, status_code=200, data={"item": "Unknown"}),
            # Escalated tier collection: succeeds
            BrightDataResponse(success=True, status_code=200, data={"title": "Item", "price": 49.0}),
        ]

        collector = BrightDataCollector(client=mock_client)
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/exhaust-healing",
            schema={"title": "string", "price": "price"},
            max_healing_attempts=2,
            max_escalation_attempts=1,
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.healing_attempts == 2
        assert result.escalation_attempts == 1
        assert result.is_verified() is True

    def test_budget_exhaustion_during_retries_halts_execution(self):
        """Verify BudgetGuard halts pipeline in BLOCKED when spending limit reached during retry/escalation."""
        # Budget allows initial scrape ($0.015), but escalation ($0.025) exceeds $0.020 cap
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.020))
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            BrightDataResponse(
                success=False,
                status_code=403,
                error="HTTP_403_FORBIDDEN",
                tier_used="scraping_browser",
            ),
        ]

        collector = BrightDataCollector(client=mock_client)
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            escalator=SentinelEscalator(),
            budget_guard=guard,
        )

        ctx = OrchestrationContext(
            url="https://example.com/budget-exhaust",
            compute_tier="scraping_browser",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert "BUDGET_EXCEEDED:USD_LIMIT" in result.failure_signature
        assert result.metadata.get("budget_exceeded") is True

    def test_preservation_of_trust_gate_behavior_across_retries(self):
        """Verify unverified data is never forwarded to AI even after multiple retries."""
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=ai_service,
        )

        ctx = OrchestrationContext(
            url="https://example.com/unverified-retries",
            schema={"title": "string"},
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert result.is_verified() is False
        assert result.ai_answer is None
        assert ai_service.calls == 0

    def test_structured_attempt_metadata_recording(self):
        """Verify attempt_history, healing_history, and escalation_history are populated in metadata."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            # Attempt 0: Missing fields -> heals
            BrightDataResponse(success=True, status_code=200, data={"title": "Item A"}),
            # Attempt 1: 403 Forbidden -> escalates
            BrightDataResponse(success=False, status_code=403, error="HTTP_403_FORBIDDEN"),
            # Attempt 2: Escalated tier succeeds
            BrightDataResponse(success=True, status_code=200, data={"title": "Item A", "price": 19.99}),
        ]

        collector = BrightDataCollector(client=mock_client)
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
            enable_cost_tracking=True,
        )

        ctx = OrchestrationContext(
            url="https://example.com/metadata-audit",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert "attempt_history" in result.metadata
        assert len(result.metadata["attempt_history"]) >= 2
        assert "healing_history" in result.metadata
        assert len(result.metadata["healing_history"]) >= 1
        assert "escalation_history" in result.metadata
        assert len(result.metadata["escalation_history"]) >= 1
