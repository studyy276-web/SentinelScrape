"""Unit and integration tests for SentinelScrape Orchestrator State Machine."""

import pytest
from app.models.response import SentinelResponse
from app.orchestrator import (
    OrchestrationContext,
    OrchestratorState,
    SentinelOrchestrator,
    StubAIService,
    StubCollector,
    StubDiagnoser,
    StubEscalator,
    StubHealer,
    StubValidator,
)


class TestOrchestratorScenarios:
    """Test suite covering primary lifecycle scenarios A through F and edge cases."""

    def test_scenario_a_first_scrape_succeeds(self):
        """Scenario A: First scrape succeeds cleanly without healing.
        Flow: START -> COLLECTING -> VALIDATING -> TRUST_GATE -> VERIFIED -> AI_READY
        """
        collector = StubCollector(data_to_return={"product": "Laptop", "price": "$999"})
        validator = StubValidator(default_passed=True, trust_score=0.95)
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=validator,
            ai_service=ai_service,
        )

        ctx = OrchestrationContext(url="https://example.com/item/1")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 0
        assert result.escalation_attempts == 0
        assert result.ai_answer is not None
        assert result.state_history == [
            OrchestratorState.START,
            OrchestratorState.COLLECTING,
            OrchestratorState.VALIDATING,
            OrchestratorState.TRUST_GATE,
            OrchestratorState.VERIFIED,
            OrchestratorState.AI_READY,
        ]

    def test_scenario_b_selector_failure_heals_on_attempt_one(self):
        """Scenario B: Selector failure triggers diagnosis, healing, and re-validation to success.
        Flow: START -> COLLECTING -> VALIDATING -> TRUST_GATE -> DIAGNOSING -> HEALING -> COLLECTING -> VALIDATING -> TRUST_GATE -> VERIFIED -> AI_READY
        """
        class DynamicCollector:
            def __init__(self):
                self.calls = 0

            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                self.calls += 1
                if context.healing_attempts == 0:
                    context.extracted_data = {"product": "Laptop"}  # Missing price
                else:
                    context.extracted_data = {"product": "Laptop", "price": "$999"}
                return context

        class DynamicValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                data = context.extracted_data or {}
                if "price" in data:
                    context.trust_score = 0.95
                    context.failed_fields = []
                    context.verification_result = {"passed": True, "score": 0.95}
                else:
                    context.trust_score = 0.3
                    context.failed_fields = ["price"]
                    context.verification_result = {"passed": False, "score": 0.3}
                return context

        diagnoser = StubDiagnoser(failure_signature="MISSING_PRICE_SELECTOR")
        healer = StubHealer(success_on_attempt=1, healing_source="CSS_FALLBACK_HEALER")
        orchestrator = SentinelOrchestrator(
            collector=DynamicCollector(),
            validator=DynamicValidator(),
            diagnoser=diagnoser,
            healer=healer,
        )

        ctx = OrchestrationContext(url="https://example.com/item/2")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 1
        assert result.failure_signature == "MISSING_PRICE_SELECTOR"
        assert result.healing_source == "CSS_FALLBACK_HEALER"
        assert OrchestratorState.DIAGNOSING in result.state_history
        assert OrchestratorState.HEALING in result.state_history

    def test_scenario_c_first_healing_fails_second_healing_succeeds(self):
        """Scenario C: First healing attempt fails, second healing attempt succeeds."""
        class MultiAttemptCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.healing_attempts < 2:
                    context.extracted_data = {"product": "Phone"}
                else:
                    context.extracted_data = {"product": "Phone", "price": "$499"}
                return context

        class DynamicValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                data = context.extracted_data or {}
                if "price" in data:
                    context.trust_score = 0.90
                    context.failed_fields = []
                    context.verification_result = {"passed": True, "score": 0.90}
                else:
                    context.trust_score = 0.2
                    context.failed_fields = ["price"]
                    context.verification_result = {"passed": False, "score": 0.2}
                return context

        orchestrator = SentinelOrchestrator(
            collector=MultiAttemptCollector(),
            validator=DynamicValidator(),
            diagnoser=StubDiagnoser(failure_signature="DYNAMIC_CONTENT_FAILURE"),
            healer=StubHealer(success_on_attempt=2),
        )

        ctx = OrchestrationContext(url="https://example.com/item/3")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 2
        assert result.escalation_attempts == 0

    def test_scenario_d_two_healing_attempts_fail_escalation_succeeds(self):
        """Scenario D: 2 healing attempts fail, subsequent escalation succeeds."""
        class TierAwareCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.compute_tier == "unblocker_browser":
                    context.extracted_data = {"product": "Protected Item", "price": "$1200"}
                else:
                    context.extracted_data = None  # Blocked or missing
                return context

        class TierAwareValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.extracted_data:
                    context.trust_score = 0.95
                    context.failed_fields = []
                    context.verification_result = {"passed": True, "score": 0.95}
                else:
                    context.trust_score = 0.1
                    context.failed_fields = ["all"]
                    context.verification_result = {"passed": False, "score": 0.1}
                return context

        orchestrator = SentinelOrchestrator(
            collector=TierAwareCollector(),
            validator=TierAwareValidator(),
            diagnoser=StubDiagnoser(failure_signature="CLOUDFLARE_CAPTCHA"),
            healer=StubHealer(success_on_attempt=99),  # Never heals via regular healer
            escalator=StubEscalator(succeed=True, target_tier="unblocker_browser"),
        )

        ctx = OrchestrationContext(url="https://example.com/item/4")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 2
        assert result.escalation_attempts == 1
        assert result.compute_tier == "unblocker_browser"
        assert OrchestratorState.ESCALATING in result.state_history

    def test_scenario_e_two_healing_attempts_fail_escalation_fails_leads_to_blocked(self):
        """Scenario E: 2 healing attempts fail, escalation fails -> BLOCKED."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=StubDiagnoser(failure_signature="HARD_BOT_DETECTION"),
            healer=StubHealer(success_on_attempt=99),
            escalator=StubEscalator(succeed=False),  # Escalation fails
        )

        ctx = OrchestrationContext(url="https://example.com/blocked")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert result.is_verified() is False
        assert result.healing_attempts == 2
        assert result.escalation_attempts == 1
        assert result.ai_answer is None
        assert OrchestratorState.BLOCKED in result.state_history

    def test_scenario_f_non_healable_failure_routes_to_escalation(self):
        """Scenario F: Non-healable failure bypasses regular healing and immediately escalates."""
        class TierAwareCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.compute_tier == "scraping_browser":
                    context.extracted_data = {"status": "unblocked_content"}
                else:
                    context.extracted_data = None
                return context

        class TierAwareValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                if context.extracted_data:
                    context.trust_score = 0.92
                    context.verification_result = {"passed": True}
                else:
                    context.trust_score = 0.0
                    context.verification_result = {"passed": False}
                return context

        diagnoser = StubDiagnoser(failure_signature="IP_RATE_LIMITED", is_healable=False)
        healer = StubHealer()
        escalator = StubEscalator(succeed=True, target_tier="scraping_browser")

        orchestrator = SentinelOrchestrator(
            collector=TierAwareCollector(),
            validator=TierAwareValidator(),
            diagnoser=diagnoser,
            healer=healer,
            escalator=escalator,
        )

        ctx = OrchestrationContext(url="https://example.com/rate-limited")
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.healing_attempts == 0  # Regular healing was bypassed
        assert result.escalation_attempts == 1
        assert healer.calls == 0  # Healer never invoked


class TestOrchestratorInvariantsAndGuards:
    """Tests strictly validating constraints, limits, and safety gates."""

    def test_healing_attempts_cannot_exceed_two(self):
        """Verify healing cannot exceed 2 attempts."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=StubDiagnoser(is_healable=True),
            healer=StubHealer(success_on_attempt=99),
            escalator=StubEscalator(succeed=False),
        )

        ctx = OrchestrationContext(url="https://example.com/limit-test")
        result = orchestrator.run(ctx)

        assert result.healing_attempts == 2
        assert result.status == OrchestratorState.BLOCKED

    def test_escalation_cannot_occur_more_than_once(self):
        """Verify escalation cannot happen more than once."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(should_fail=True),
            validator=StubValidator(default_passed=False),
            diagnoser=StubDiagnoser(is_healable=False),
            escalator=StubEscalator(succeed=False),
        )

        ctx = OrchestrationContext(url="https://example.com/escalation-limit")
        result = orchestrator.run(ctx)

        assert result.escalation_attempts == 1
        assert result.status == OrchestratorState.BLOCKED

    def test_ai_ready_cannot_be_reached_without_verification(self):
        """Ensure an unverified context cannot execute AI generation."""
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(ai_service=ai_service)

        ctx = OrchestrationContext(
            url="https://example.com/unverified",
            status=OrchestratorState.AI_READY,
            trust_score=0.2,
            verification_result={"passed": False},
        )

        with pytest.raises(RuntimeError, match="Security violation|Security and Trust violation"):
            orchestrator.step(ctx)

    def test_unverified_extracted_data_never_sent_to_ai_service(self):
        """Verify StubAIService asserts caller verification."""
        ai_service = StubAIService()
        unverified_ctx = OrchestrationContext(
            extracted_data={"sensitive": "dirty_data"},
            trust_score=0.4,
            verification_result={"passed": False},
        )

        with pytest.raises(RuntimeError, match="AIService received unverified data"):
            ai_service.generate_answer(unverified_ctx)
        assert len(ai_service.received_data) == 0

    def test_failure_signature_survives_diagnosis_and_healing(self):
        """Verify failure_signature is preserved through the lifecycle."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"title": "Recovered"}),
            validator=StubValidator(default_passed=True),
            diagnoser=StubDiagnoser(failure_signature="STRUCTURAL_DOM_MUTATION"),
            healer=StubHealer(healing_source="AI_SYNTHESIZED_SELECTOR"),
        )

        ctx = OrchestrationContext(
            url="https://example.com/mutation",
            status=OrchestratorState.DIAGNOSING,
        )
        ctx = orchestrator.step(ctx)  # DIAGNOSING -> HEALING
        assert ctx.failure_signature == "STRUCTURAL_DOM_MUTATION"

        ctx = orchestrator.step(ctx)  # HEALING -> COLLECTING
        assert ctx.failure_signature == "STRUCTURAL_DOM_MUTATION"
        assert ctx.healing_source == "AI_SYNTHESIZED_SELECTOR"

    def test_successful_healing_resets_flow_through_validation(self):
        """Verify successful healing resets context back to COLLECTING -> VALIDATING -> TRUST_GATE."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"valid": True}),
            validator=StubValidator(default_passed=True),
            diagnoser=StubDiagnoser(),
            healer=StubHealer(),
        )

        ctx = OrchestrationContext(status=OrchestratorState.HEALING)
        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.COLLECTING

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.VALIDATING

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.TRUST_GATE

    def test_blocked_state_prevents_ai_execution(self):
        """Ensure terminal BLOCKED state never invokes AI Service."""
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(ai_service=ai_service)

        ctx = OrchestrationContext(
            url="https://example.com/blocked",
            status=OrchestratorState.BLOCKED,
            ai_answer=None,
        )
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.BLOCKED
        assert result.ai_answer is None
        assert ai_service.calls == 0

    def test_sentinel_response_contract_compatibility(self):
        """Verify OrchestrationContext converts cleanly to SentinelResponse without missing fields."""
        ctx = OrchestrationContext(
            collector_id="col_123",
            url="https://example.com/item",
            schema={"type": "object", "properties": {"title": {"type": "string"}}},
            extracted_data={"title": "Test Title"},
            trust_score=0.98,
            status=OrchestratorState.AI_READY,
            failure_signature="NONE",
            failed_fields=[],
            healing_source="ORIGINAL_SELECTOR",
            compute_tier="standard",
            healing_attempts=1,
            verification_result={"passed": True, "score": 0.98},
            cost_ledger={"tokens": 120, "usd": 0.002},
            ai_answer="Here is your verified summary.",
        )

        response = ctx.to_response()
        assert isinstance(response, SentinelResponse)
        assert response.collector_id == "col_123"
        assert response.url == "https://example.com/item"
        assert response.schema == {"type": "object", "properties": {"title": {"type": "string"}}}
        assert response.extracted_data == {"title": "Test Title"}
        assert response.trust_score == 0.98
        assert response.status == "AI_READY"
        assert response.failure_signature == "NONE"
        assert response.failed_fields == []
        assert response.healing_source == "ORIGINAL_SELECTOR"
        assert response.compute_tier == "standard"
        assert response.healing_attempts == 1
        assert response.verification_result == {"passed": True, "score": 0.98}
        assert response.cost_ledger == {"tokens": 120, "usd": 0.002}
        assert response.ai_answer == "Here is your verified summary."

    def test_step_by_step_execution_granularity(self):
        """Verify granular step() advances state one transition at a time."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(),
            validator=StubValidator(default_passed=True),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(url="https://example.com/step-by-step")
        assert ctx.status == OrchestratorState.START

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.COLLECTING

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.VALIDATING

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.TRUST_GATE

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.VERIFIED

        ctx = orchestrator.step(ctx)
        assert ctx.status == OrchestratorState.AI_READY

        ctx = orchestrator.step(ctx)
        assert ctx.ai_answer is not None

    def test_custom_ai_prompt_forwarding(self):
        """Verify custom AI prompt is passed through orchestrator to AI service."""
        ai_service = StubAIService()
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"stock": 42}),
            validator=StubValidator(default_passed=True),
            ai_service=ai_service,
        )

        ctx = OrchestrationContext(
            url="https://example.com/prompt",
            ai_prompt="Summarize the stock levels",
        )
        result = orchestrator.run(ctx)

        assert "Summarize the stock levels" in (result.ai_answer or "")


class TestFixRegressions:
    """Regression tests covering Claude code review Fixes 1 through 4."""

    # --- FIX 1: Fail-Closed Escalation ---

    def test_escalation_fail_closed_without_explicit_success_flag(self):
        """FIX 1: Escalator that does not set escalation_succeeded=True transitions to BLOCKED."""
        class SilentEscalator:
            def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
                # Does NOT set metadata["escalation_succeeded"] = True
                context.compute_tier = "scraping_browser"
                return context

        orchestrator = SentinelOrchestrator(escalator=SilentEscalator())
        ctx = OrchestrationContext(
            url="https://example.com/blocked",
            status=OrchestratorState.ESCALATING,
        )
        result = orchestrator.step(ctx)
        assert result.status == OrchestratorState.BLOCKED
        assert result.escalation_attempts == 1

    def test_escalation_succeeds_when_explicitly_set(self):
        """FIX 1: Escalator with explicit escalation_succeeded=True transitions to COLLECTING."""
        class ExplicitSuccessEscalator:
            def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
                context.metadata["escalation_succeeded"] = True
                return context

        orchestrator = SentinelOrchestrator(escalator=ExplicitSuccessEscalator())
        ctx = OrchestrationContext(
            url="https://example.com/item",
            status=OrchestratorState.ESCALATING,
        )
        result = orchestrator.step(ctx)
        assert result.status == OrchestratorState.COLLECTING
        assert result.escalation_attempts == 1

    # --- FIX 2: Single Trust-Gate Source of Truth ---

    def test_verification_result_passed_false_cannot_reach_ai(self):
        """FIX 2: verification_result passed=False prevents reaching VERIFIED / AI."""
        orchestrator = SentinelOrchestrator()
        ctx = OrchestrationContext(
            verification_result={"passed": False, "score": 0.99},
            trust_score=0.99,
            failed_fields=[],
            status=OrchestratorState.TRUST_GATE,
        )
        assert ctx.is_verified() is False
        result = orchestrator.step(ctx)
        assert result.status != OrchestratorState.VERIFIED
        assert result.status != OrchestratorState.AI_READY

    def test_high_trust_score_alone_cannot_reach_ai(self):
        """FIX 2: High trust_score alone without passed verification_result cannot reach AI."""
        orchestrator = SentinelOrchestrator()
        ctx = OrchestrationContext(
            verification_result=None,
            trust_score=0.99,
            failed_fields=[],
            status=OrchestratorState.TRUST_GATE,
        )
        assert ctx.is_verified() is False
        result = orchestrator.step(ctx)
        assert result.status != OrchestratorState.VERIFIED
        assert result.status != OrchestratorState.AI_READY

    def test_failed_fields_empty_alone_cannot_reach_ai(self):
        """FIX 2: Empty failed_fields alone without passed verification_result cannot reach AI."""
        orchestrator = SentinelOrchestrator()
        ctx = OrchestrationContext(
            verification_result=None,
            failed_fields=[],
            trust_score=0.0,
            status=OrchestratorState.TRUST_GATE,
        )
        assert ctx.is_verified() is False
        result = orchestrator.step(ctx)
        assert result.status != OrchestratorState.VERIFIED
        assert result.status != OrchestratorState.AI_READY

    def test_verification_result_passed_true_can_reach_verified_and_ai_ready(self):
        """FIX 2: verification_result passed=True reaches VERIFIED and then AI_READY."""
        orchestrator = SentinelOrchestrator()
        ctx = OrchestrationContext(
            extracted_data={"title": "Verified Product"},
            verification_result={"passed": True, "score": 0.95},
            status=OrchestratorState.TRUST_GATE,
        )
        assert ctx.is_verified() is True
        result = orchestrator.step(ctx)
        assert result.status == OrchestratorState.VERIFIED
        result = orchestrator.step(result)
        assert result.status == OrchestratorState.AI_READY

    # --- FIX 3: Exception Handling ---

    def test_collector_exception_handling(self):
        """FIX 3: Collector exception transitions to FAILED and records error in metadata."""
        class FaultyCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                raise ConnectionError("Network connection reset by peer")

        orchestrator = SentinelOrchestrator(collector=FaultyCollector())
        ctx = OrchestrationContext(status=OrchestratorState.COLLECTING)
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.FAILED
        assert "Network connection reset by peer" in result.metadata.get("error", "")
        assert OrchestratorState.FAILED in result.state_history

    def test_validator_exception_handling(self):
        """FIX 3: Validator exception transitions to FAILED and records error in metadata."""
        class FaultyValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                raise ValueError("Corrupt schema validation rule")

        orchestrator = SentinelOrchestrator(validator=FaultyValidator())
        ctx = OrchestrationContext(status=OrchestratorState.VALIDATING)
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.FAILED
        assert "Corrupt schema validation rule" in result.metadata.get("error", "")

    def test_diagnoser_exception_handling(self):
        """FIX 3: Diagnoser exception transitions to FAILED and records error in metadata."""
        class FaultyDiagnoser:
            def diagnose(self, context: OrchestrationContext) -> OrchestrationContext:
                raise RuntimeError("Diagnosis rule engine crashed")

        orchestrator = SentinelOrchestrator(diagnoser=FaultyDiagnoser())
        ctx = OrchestrationContext(status=OrchestratorState.DIAGNOSING)
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.FAILED
        assert "Diagnosis rule engine crashed" in result.metadata.get("error", "")

    def test_healer_exception_handling(self):
        """FIX 3: Healer exception transitions to FAILED and records error in metadata."""
        class FaultyHealer:
            def heal(self, context: OrchestrationContext) -> OrchestrationContext:
                raise KeyError("Selector synthesis database missing")

        orchestrator = SentinelOrchestrator(healer=FaultyHealer())
        ctx = OrchestrationContext(status=OrchestratorState.HEALING)
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.FAILED
        assert "Selector synthesis database missing" in result.metadata.get("error", "")

    def test_escalation_exception_handling(self):
        """FIX 3: Escalator exception transitions to FAILED and records error in metadata."""
        class FaultyEscalator:
            def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
                raise TimeoutError("Escalation proxy timeout")

        orchestrator = SentinelOrchestrator(escalator=FaultyEscalator())
        ctx = OrchestrationContext(status=OrchestratorState.ESCALATING)
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.FAILED
        assert "Escalation proxy timeout" in result.metadata.get("error", "")

    def test_ai_service_exception_handling(self):
        """FIX 3: AI Service exception transitions to FAILED and records error in metadata."""
        class FaultyAIService:
            def generate_answer(self, context: OrchestrationContext, prompt=None) -> OrchestrationContext:
                raise RuntimeError("LLM rate limit exceeded")

        orchestrator = SentinelOrchestrator(ai_service=FaultyAIService())
        ctx = OrchestrationContext(
            extracted_data={"title": "Clean item"},
            verification_result={"passed": True},
            status=OrchestratorState.AI_READY,
        )
        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.FAILED
        assert "LLM rate limit exceeded" in result.metadata.get("error", "")
        assert result.ai_answer is None

    # --- FIX 4: Clear Stale Data Before New Collection ---

    def test_stale_verification_result_cleared_before_new_collection_after_healing(self):
        """FIX 4: Stale verification_result and extracted data are cleared when retrying after HEALING."""
        class MockHealer:
            def heal(self, context: OrchestrationContext) -> OrchestrationContext:
                return context

        orchestrator = SentinelOrchestrator(healer=MockHealer())
        ctx = OrchestrationContext(
            url="https://example.com/retry-heal",
            schema={"type": "object"},
            collector_id="col_42",
            failure_signature="OLD_SIG",
            extracted_data={"stale": "data"},
            trust_score=0.75,
            failed_fields=["price"],
            verification_result={"passed": False, "stale": True},
            cost_ledger={"usd": 0.01},
            status=OrchestratorState.HEALING,
            healing_attempts=0,
        )

        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.COLLECTING
        assert result.verification_result is None
        assert result.extracted_data is None
        assert result.trust_score == 0.0
        assert result.failed_fields == []
        # Preserved fields
        assert result.url == "https://example.com/retry-heal"
        assert result.schema == {"type": "object"}
        assert result.collector_id == "col_42"
        assert result.failure_signature == "OLD_SIG"
        assert result.healing_attempts == 1
        assert result.cost_ledger == {"usd": 0.01}

    def test_stale_verification_result_cleared_before_new_collection_after_escalating(self):
        """FIX 4: Stale verification_result and extracted data are cleared when retrying after ESCALATING."""
        class MockEscalator:
            def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
                context.metadata["escalation_succeeded"] = True
                return context

        orchestrator = SentinelOrchestrator(escalator=MockEscalator())
        ctx = OrchestrationContext(
            url="https://example.com/retry-esc",
            extracted_data={"stale": "data"},
            trust_score=0.5,
            verification_result={"passed": False, "score": 0.5},
            status=OrchestratorState.ESCALATING,
            escalation_attempts=0,
        )

        result = orchestrator.step(ctx)

        assert result.status == OrchestratorState.COLLECTING
        assert result.verification_result is None
        assert result.extracted_data is None
        assert result.trust_score == 0.0
        assert result.is_verified() is False
        assert result.escalation_attempts == 1
