"""Unit and integration tests for SentinelHealer, SentinelEscalator, and self-healing strategies."""

import pytest
from app.healing.escalator import DEFAULT_TIER_ORDER, SentinelEscalator
from app.healing.healer import SentinelHealer
from app.healing.strategies import HealingStrategy
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import (
    Escalator,
    Healer,
    StubAIService,
    StubCollector,
)
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator
from app.diagnosis.diagnoser import SentinelDiagnoser


class TestSentinelHealerUnit:
    """Unit tests for SentinelHealer strategy selection, value normalization, and metadata tracking."""

    def setup_method(self):
        self.healer = SentinelHealer()

    def test_healer_protocol_compliance(self):
        """Verify SentinelHealer conforms to the Healer runtime protocol."""
        assert isinstance(self.healer, Healer)

    def test_strategy_selection_based_on_failure_signature(self):
        """Verify optimal strategy selection for different failure types."""
        # 1. Selector / Missing fields
        assert self.healer.select_strategy(failure_signature="MISSING_FIELD:price", attempt=1) == HealingStrategy.CSS_SELECTOR_FALLBACK
        assert self.healer.select_strategy(failure_signature="MISSING_FIELD:price", attempt=2) == HealingStrategy.DOM_HEURISTIC_PARSER

        # 2. Type / Price formatting issues
        assert self.healer.select_strategy(failure_signature="INVALID_PRICE:price") == HealingStrategy.DATA_NORMALIZATION
        assert self.healer.select_strategy(failure_signature="TYPE_MISMATCH:rating") == HealingStrategy.DATA_NORMALIZATION

        # 3. Dynamic content
        assert self.healer.select_strategy(failure_signature="DYNAMIC_WAIT_TIMEOUT") == HealingStrategy.DYNAMIC_WAIT_ADJUST

    def test_value_normalization_heuristics(self):
        """Verify normalization coercions for price, numbers, and strings."""
        assert self.healer.normalize_value("price", "$1,499.99 USD", rule="price") == 1499.99
        assert self.healer.normalize_value("price", "  29.95  ", rule="price") == 29.95
        assert self.healer.normalize_value("rating", "4.8 / 5.0", rule="rating") == 4.8
        assert self.healer.normalize_value("item_count", " 150 items ", rule="number") == 150
        assert self.healer.normalize_value("title", "  Cleaned Title  ", rule="string") == "Cleaned Title"

    def test_healer_execution_updates_context(self):
        """Verify heal() attaches healing source, adjustments, and metadata."""
        ctx = OrchestrationContext(
            url="https://example.com/item",
            failure_signature="MISSING_FIELD:title, MISSING_FIELD:price",
            failed_fields=["title", "price"],
            healing_attempts=1,
            status=OrchestratorState.HEALING,
        )

        result = self.healer.heal(ctx)

        assert result.healing_source == HealingStrategy.CSS_SELECTOR_FALLBACK.value
        assert result.metadata.get("healed") is True
        assert "healing_details" in result.metadata
        assert "selector_fallbacks" in result.metadata["healing_details"]["adjustments"]
        assert "title" in result.metadata["healing_details"]["adjustments"]["selector_fallbacks"]


class TestSentinelEscalatorUnit:
    """Unit tests for SentinelEscalator tier progression and anti-bot escalation."""

    def setup_method(self):
        self.escalator = SentinelEscalator()

    def test_escalator_protocol_compliance(self):
        """Verify SentinelEscalator conforms to the Escalator runtime protocol."""
        assert isinstance(self.escalator, Escalator)

    def test_sequential_tier_progression(self):
        """Verify standard sequential tier progression."""
        assert self.escalator.determine_next_tier("standard") == "residential_proxy"
        assert self.escalator.determine_next_tier("residential_proxy") == "scraping_browser"
        assert self.escalator.determine_next_tier("scraping_browser") == "unblocker_browser"
        assert self.escalator.determine_next_tier("unblocker_browser") == "premium"
        assert self.escalator.determine_next_tier("premium") == "premium"

    def test_antibot_blocking_fast_track_escalation(self):
        """Verify 403 / Cloudflare / CAPTCHA failures immediately jump to unblocker_browser."""
        assert self.escalator.determine_next_tier("standard", failure_signature="HTTP_403_FORBIDDEN") == "unblocker_browser"
        assert self.escalator.determine_next_tier("residential_proxy", failure_signature="CLOUDFLARE_CHALLENGE") == "unblocker_browser"
        assert self.escalator.determine_next_tier("scraping_browser", failure_signature="CAPTCHA_DETECTED") == "unblocker_browser"
        assert self.escalator.determine_next_tier("unblocker_browser", failure_signature="HTTP_403_FORBIDDEN") == "premium"

    def test_escalator_execution_updates_context(self):
        """Verify escalate() updates compute_tier and metadata."""
        ctx = OrchestrationContext(
            url="https://example.com/protected",
            compute_tier="standard",
            failure_signature="HTTP_403_FORBIDDEN",
            escalation_attempts=1,
            status=OrchestratorState.ESCALATING,
        )

        result = self.escalator.escalate(ctx)

        assert result.compute_tier == "unblocker_browser"
        assert result.metadata.get("escalation_succeeded") is True
        assert result.metadata["escalation_details"]["from_tier"] == "standard"
        assert result.metadata["escalation_details"]["to_tier"] == "unblocker_browser"


class TestHealingAndEscalationPipelineIntegration:
    """Integration tests executing full pipeline with SentinelHealer, SentinelEscalator, Diagnoser, and Validator."""

    def test_real_healer_recovers_missing_data_in_state_machine(self):
        """Verify state machine uses SentinelHealer to recover data across retry attempts."""
        class DynamicHealableCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.healing_attempts == 0:
                    # Initial attempt: missing price fails validation
                    context.extracted_data = {"title": "Noise-Canceling Earbuds"}
                else:
                    # After healing attempt, collector uses fallback selector to extract price
                    context.extracted_data = {"title": "Noise-Canceling Earbuds", "price": 129.99}
                return context

        orchestrator = SentinelOrchestrator(
            collector=DynamicHealableCollector(),
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/earbuds",
            schema={
                "title": {"type": "string", "required": True},
                "price": {"type": "price", "required": True},
            },
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 1
        assert result.extracted_data == {"title": "Noise-Canceling Earbuds", "price": 129.99}

    def test_real_escalator_recovers_antibot_blocking_in_state_machine(self):
        """Verify state machine uses SentinelEscalator to upgrade tier and succeed on escalated collection."""
        class EscalatingCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.compute_tier == "standard":
                    context.extracted_data = None
                    context.failure_signature = "HTTP_403_FORBIDDEN"
                else:
                    # Escalated tier succeeds
                    context.extracted_data = {"title": "Secured Gadget", "price": 99.50}
                    context.failure_signature = None
                return context

        orchestrator = SentinelOrchestrator(
            collector=EscalatingCollector(),
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/anti-bot-gadget",
            compute_tier="standard",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.escalation_attempts == 1
        assert result.compute_tier == "unblocker_browser"
        assert result.is_verified() is True
        assert result.ai_answer is not None
