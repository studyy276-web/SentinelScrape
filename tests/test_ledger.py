"""Unit and integration tests for CostLedger, BudgetGuard, and Orchestrator cost tracking."""

import pytest
from app.ledger.budget_guard import BudgetConfig, BudgetGuard
from app.ledger.cost_ledger import CostLedger, TierPricing
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


class TestCostLedger:
    """Unit tests for CostLedger tracking and calculations."""

    def test_cost_ledger_initialization_and_defaults(self):
        """Verify CostLedger initializes with zero metrics and standard tier rates."""
        ledger = CostLedger()
        assert ledger.total_cost_usd == 0.0
        assert ledger.total_tokens == 0
        assert ledger.api_calls == 0
        assert ledger.get_tier_rate("standard") == 0.001
        assert ledger.get_tier_rate("scraping_browser") == 0.015
        assert ledger.get_tier_rate("unblocker_browser") == 0.025

    def test_record_collection_accumulates_cost_and_calls(self):
        """Verify collection recording tracks tier usage and updates breakdown."""
        ledger = CostLedger()
        cost1 = ledger.record_collection(tier="standard")
        assert cost1 == 0.001
        assert ledger.total_cost_usd == 0.001
        assert ledger.api_calls == 1
        assert ledger.tier_usage["standard"] == 1

        cost2 = ledger.record_collection(tier="unblocker_browser")
        assert cost2 == 0.025
        assert ledger.total_cost_usd == 0.026
        assert ledger.api_calls == 2
        assert ledger.tier_usage["unblocker_browser"] == 1
        assert ledger.breakdown["collection"] == 0.026

    def test_record_healing_and_escalation(self):
        """Verify healing and escalation tracking."""
        ledger = CostLedger()
        heal_cost = ledger.record_healing(source="CSS_FALLBACK_SELECTOR", tokens=50)
        assert heal_cost == 0.002
        assert ledger.total_tokens == 50
        assert ledger.breakdown["healing"] == 0.002

        esc_cost = ledger.record_escalation(from_tier="standard", to_tier="scraping_browser")
        assert esc_cost == 0.014  # 0.015 - 0.001
        assert ledger.breakdown["escalation"] == 0.014
        assert ledger.total_cost_usd == 0.016

    def test_record_ai_generation_tokens_and_cost(self):
        """Verify AI token usage and inference cost calculation."""
        ledger = CostLedger()
        # 1000 prompt tokens ($0.0001) + 500 completion tokens ($0.0002) = $0.0003
        ai_cost = ledger.record_ai_generation(prompt_tokens=1000, completion_tokens=500)
        assert round(ai_cost, 4) == 0.0003
        assert ledger.total_tokens == 1500
        assert ledger.prompt_tokens == 1000
        assert ledger.completion_tokens == 500
        assert ledger.api_calls == 1
        assert ledger.breakdown["ai"] == 0.0003

    def test_to_dict_and_hydration(self):
        """Verify serialization and re-hydration from dictionary."""
        ledger1 = CostLedger()
        ledger1.record_collection("standard")
        ledger1.record_ai_generation(200, 100)

        data = ledger1.to_dict()
        assert "total_cost_usd" in data
        assert "total_tokens" in data
        assert "events" in data
        assert len(data["events"]) == 2

        # Hydrate into fresh ledger
        ledger2 = CostLedger(initial_data=data)
        assert ledger2.total_cost_usd == ledger1.total_cost_usd
        assert ledger2.total_tokens == ledger1.total_tokens
        assert ledger2.api_calls == ledger1.api_calls


class TestBudgetGuard:
    """Unit tests for BudgetGuard limit enforcement."""

    def test_can_afford_within_limits(self):
        """Verify can_afford returns True when under limits."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.05, max_total_tokens=1000))
        ledger = CostLedger()
        ledger.record_collection("standard")  # $0.001

        can_afford, reason = guard.can_afford(ledger, proposed_cost_usd=0.010, proposed_tokens=100)
        assert can_afford is True
        assert reason is None

    def test_usd_budget_exceeded(self):
        """Verify exceeding USD limit returns False and reason tag."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.02))
        ledger = CostLedger()
        ledger.record_collection("unblocker_browser")  # $0.025 > $0.020

        can_afford, reason = guard.can_afford(ledger)
        assert can_afford is False
        assert "BUDGET_EXCEEDED:USD_LIMIT" in reason

    def test_token_budget_exceeded(self):
        """Verify exceeding token limit returns False and reason tag."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=1.0, max_total_tokens=500))
        ledger = CostLedger()
        ledger.record_ai_generation(prompt_tokens=400, completion_tokens=200)  # 600 > 500

        can_afford, reason = guard.can_afford(ledger)
        assert can_afford is False
        assert "BUDGET_EXCEEDED:TOKEN_LIMIT" in reason

    def test_enforce_transitions_context_to_blocked(self):
        """Verify enforce() transitions context to BLOCKED and records failure signature."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.01))
        ledger = CostLedger()
        ledger.record_collection("unblocker_browser")  # $0.025 > $0.010

        ctx = OrchestrationContext(status=OrchestratorState.COLLECTING)
        result = guard.enforce(ctx, ledger=ledger)

        assert result is False
        assert ctx.status == OrchestratorState.BLOCKED
        assert "BUDGET_EXCEEDED:USD_LIMIT" in ctx.failure_signature
        assert ctx.metadata.get("budget_exceeded") is True


class TestOrchestratorCostAndBudgetIntegration:
    """Integration tests for state machine execution with BudgetGuard and CostLedger."""

    def test_standard_orchestration_accumulates_cost_ledger(self):
        """Verify successful end-to-end orchestration populates cost ledger at all active stages."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=1.0))
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"title": "Widget", "price": "$10"}),
            validator=StubValidator(default_passed=True),
            ai_service=StubAIService(default_answer="Insight summary."),
            budget_guard=guard,
        )

        ctx = OrchestrationContext(url="https://example.com/item")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.cost_ledger is not None
        assert result.cost_ledger["total_cost_usd"] > 0.0
        assert result.cost_ledger["api_calls"] >= 2  # Collection + AI
        assert "collection" in result.cost_ledger["breakdown"]
        assert "ai" in result.cost_ledger["breakdown"]

    def test_budget_exceeded_before_collection_blocks_pipeline(self):
        """Verify strict budget blocks execution immediately before costly initial collection."""
        # Budget is only $0.0005, but standard collection costs $0.001
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.0005))
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(),
            validator=StubValidator(),
            budget_guard=guard,
        )

        ctx = OrchestrationContext(url="https://example.com/tight-budget")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert "BUDGET_EXCEEDED" in result.failure_signature
        assert result.metadata.get("budget_exceeded") is True

    def test_budget_exceeded_before_escalation_blocks_pipeline(self):
        """Verify pipeline stops at BLOCKED when tier escalation exceeds remaining budget."""
        # Budget is $0.005. Initial scrape ($0.001) passes, but escalation ($0.015) exceeds $0.005
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.005))
        collector = StubCollector(should_fail=True)
        validator = StubValidator(default_passed=False)
        diagnoser = StubDiagnoser(is_healable=False)  # Routes to escalation
        escalator = StubEscalator(succeed=True)

        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=validator,
            diagnoser=diagnoser,
            escalator=escalator,
            budget_guard=guard,
        )

        ctx = OrchestrationContext(url="https://example.com/blocked-escalation")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert "BUDGET_EXCEEDED" in result.failure_signature
        assert result.metadata.get("budget_exceeded") is True

    def test_budget_exceeded_before_ai_generation_blocks_execution(self):
        """Verify unverified or out-of-budget AI stage is prevented."""
        # Budget allows collection ($0.001) but limits tokens/spend so AI generation is blocked
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.0011))
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(),
            validator=StubValidator(default_passed=True),
            ai_service=ai_service,
            budget_guard=guard,
        )

        ctx = OrchestrationContext(
            url="https://example.com/ai-budget",
            # Pre-load ledger so collection takes it right to the brink
            cost_ledger={"total_cost_usd": 0.0008},
        )
        result = orchestrator.run(ctx)

        # Proposed AI cost ($0.0005) + total ($0.0018) > $0.0011 limit
        assert result.status == OrchestratorState.BLOCKED
        assert "BUDGET_EXCEEDED" in result.failure_signature
        assert ai_service.calls == 0

    def test_multi_healing_loop_accumulates_cumulative_cost(self):
        """Verify multi-attempt healing correctly aggregates cumulative cost across retries."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=1.0))

        class MultiAttemptCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.healing_attempts < 2:
                    context.extracted_data = {"product": "Phone"}  # Missing price
                else:
                    context.extracted_data = {"product": "Phone", "price": "$499"}
                return context

        class DynamicValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                data = context.extracted_data or {}
                if "price" in data:
                    context.trust_score = 0.95
                    context.verification_result = {"passed": True}
                else:
                    context.trust_score = 0.3
                    context.verification_result = {"passed": False}
                return context

        orchestrator = SentinelOrchestrator(
            collector=MultiAttemptCollector(),
            validator=DynamicValidator(),
            diagnoser=StubDiagnoser(is_healable=True),
            healer=StubHealer(success_on_attempt=2),
            ai_service=StubAIService(),
            budget_guard=guard,
        )

        ctx = OrchestrationContext(url="https://example.com/multi-heal")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.healing_attempts == 2
        # 3 collections (initial + retry 1 + retry 2) + 2 healings + 1 AI call
        assert result.cost_ledger["tier_usage"]["standard"] == 3
        assert result.cost_ledger["breakdown"]["healing"] > 0.0
        assert result.cost_ledger["total_cost_usd"] > 0.005
