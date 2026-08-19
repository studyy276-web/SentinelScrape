"""Comprehensive mocked integration tests for SentinelScrape pipeline with Bright Data Browser API."""

from unittest.mock import MagicMock, patch
import pytest

from app.diagnosis.diagnoser import SentinelDiagnoser
from app.integrations.brightdata.client import BrightDataClient, BrightDataResponse
from app.integrations.brightdata.collector import BrightDataCollector
from app.ledger.budget_guard import BudgetConfig, BudgetGuard
from app.ledger.cost_ledger import CostLedger
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import (
    StubAIService,
    StubEscalator,
    StubHealer,
)
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator


class TestBrightDataPipelineIntegration:
    """Integration test suite connecting BrightDataCollector to Validator, Diagnoser, CostLedger, and AI stage."""

    def test_end_to_end_successful_scraping_and_validation(self):
        """Verify successful Bright Data extraction advances through validation to AI_READY with cost tracking."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=True,
            status_code=200,
            data={"title": "Wireless Ergonomic Mouse", "price": 49.99, "rating": 4.8},
            raw_content="<html><body>Mouse $49.99</body></html>",
            tier_used="scraping_browser",
            response_time_ms=350.0,
        )

        collector = BrightDataCollector(client=mock_client)
        validator = SentinelValidator()
        diagnoser = SentinelDiagnoser()
        ai_service = StubAIService(default_answer="AI Summary of verified product.")
        budget_guard = BudgetGuard(config=BudgetConfig(max_budget_usd=1.0))

        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=validator,
            diagnoser=diagnoser,
            ai_service=ai_service,
            budget_guard=budget_guard,
        )

        ctx = OrchestrationContext(
            url="https://example.com/mouse",
            schema={
                "title": {"type": "string", "required": True},
                "price": {"type": "price", "required": True},
                "rating": {"type": "rating", "required": True},
            },
            ai_prompt="Analyze mouse specifications",
        )

        result = orchestrator.run(ctx)

        # 1. State machine completed to AI_READY
        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.trust_score == 100.0
        assert result.verification_result["passed"] is True

        # 2. AI generation verified
        assert result.ai_answer is not None
        assert "AI Summary of verified product" in result.ai_answer
        assert ai_service.calls == 1

        # 3. Cost ledger recorded costs for collection and AI
        assert result.cost_ledger is not None
        assert result.cost_ledger["total_cost_usd"] > 0.0
        assert "collection" in result.cost_ledger["breakdown"]
        assert "ai" in result.cost_ledger["breakdown"]
        assert result.cost_ledger["api_calls"] >= 2

    def test_brightdata_403_blocking_failure_routes_to_diagnosis_and_escalation(self):
        """Verify Bright Data 403 / anti-bot failure is categorized as non-healable and routes to escalation."""
        mock_client = MagicMock(spec=BrightDataClient)
        # 1st attempt: 403 Forbidden (triggers diagnosis -> escalation)
        # 2nd attempt: Success on escalated tier
        mock_client.scrape.side_effect = [
            BrightDataResponse(
                success=False,
                status_code=403,
                error="HTTP_403_FORBIDDEN: Cloudflare protection",
                tier_used="standard",
            ),
            BrightDataResponse(
                success=True,
                status_code=200,
                data={"title": "Protected Item", "price": 99.0},
                tier_used="unblocker_browser",
            ),
        ]

        collector = BrightDataCollector(client=mock_client)
        validator = SentinelValidator()
        diagnoser = SentinelDiagnoser()
        escalator = StubEscalator(succeed=True, target_tier="unblocker_browser")
        ai_service = StubAIService()

        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=validator,
            diagnoser=diagnoser,
            escalator=escalator,
            ai_service=ai_service,
            budget_guard=BudgetGuard(config=BudgetConfig(max_budget_usd=1.0)),
        )

        ctx = OrchestrationContext(
            url="https://example.com/protected-store",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.escalation_attempts == 1
        assert result.compute_tier == "unblocker_browser"
        assert result.is_verified() is True
        assert result.ai_answer is not None

    def test_brightdata_missing_fields_routes_to_healing_loop(self):
        """Verify healable extraction failures (missing fields) trigger healing loop before succeeding."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            # 1st attempt: missing price
            BrightDataResponse(
                success=True,
                status_code=200,
                data={"title": "Product A"},
                tier_used="scraping_browser",
            ),
            # 2nd attempt after healing: complete data
            BrightDataResponse(
                success=True,
                status_code=200,
                data={"title": "Product A", "price": 29.99},
                tier_used="scraping_browser",
            ),
        ]

        collector = BrightDataCollector(client=mock_client)
        validator = SentinelValidator()
        diagnoser = SentinelDiagnoser()
        healer = StubHealer(success_on_attempt=1)
        ai_service = StubAIService()

        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=validator,
            diagnoser=diagnoser,
            healer=healer,
            ai_service=ai_service,
            budget_guard=BudgetGuard(config=BudgetConfig(max_budget_usd=1.0)),
        )

        ctx = OrchestrationContext(
            url="https://example.com/partial-item",
            schema={
                "title": {"type": "string", "required": True},
                "price": {"type": "price", "required": True},
            },
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.healing_attempts == 1
        assert result.is_verified() is True
        assert result.extracted_data == {"title": "Product A", "price": 29.99}

    def test_unverified_brightdata_data_never_sent_to_ai(self):
        """Verify strict invariant: unverified or invalid Bright Data content never reaches AI stage."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=False,
            status_code=502,
            error="NETWORK_ERROR: Connection failed",
            tier_used="scraping_browser",
        )

        collector = BrightDataCollector(client=mock_client)
        validator = SentinelValidator()
        diagnoser = SentinelDiagnoser()
        ai_service = StubAIService()

        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=validator,
            diagnoser=diagnoser,
            ai_service=ai_service,
            # No escalator or healer to recover
        )

        ctx = OrchestrationContext(url="https://example.com/unreachable")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert result.is_verified() is False
        assert result.ai_answer is None
        assert ai_service.calls == 0

    def test_budget_guard_blocks_execution_before_costly_collection(self):
        """Verify BudgetGuard blocks orchestrator pipeline when proposed spend exceeds cap."""
        # Budget cap is $0.005, but scraping_browser tier costs $0.015
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.005))
        mock_client = MagicMock(spec=BrightDataClient)

        collector = BrightDataCollector(client=mock_client)
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=SentinelValidator(),
            budget_guard=guard,
        )

        ctx = OrchestrationContext(
            url="https://example.com/tight-budget",
            compute_tier="scraping_browser",
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert "BUDGET_EXCEEDED:USD_LIMIT" in result.failure_signature
        assert result.metadata.get("budget_exceeded") is True
        # Verify no scrape was executed
        assert mock_client.scrape.called is False
