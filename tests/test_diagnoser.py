"""Unit and integration tests for FailureClassifier and SentinelDiagnoser."""

import pytest
from app.diagnosis.classifier import (
    FailureCategory,
    FailureClassification,
    FailureClassifier,
    HEALABLE_CATEGORIES,
)
from app.diagnosis.diagnoser import SentinelDiagnoser
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import (
    StubAIService,
    StubCollector,
    StubEscalator,
    StubHealer,
    StubValidator,
)
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator


class TestFailureClassifier:
    """Unit test suite for FailureClassifier tag parsing, categorization, and precedence."""

    def setup_method(self):
        self.classifier = FailureClassifier()

    def test_schema_extraction_failures_are_healable(self):
        """Verify MISSING_FIELD, SELECTOR, DOM, and extraction errors classify as SCHEMA_EXTRACTION and are healable."""
        signatures = [
            "MISSING_FIELD:price",
            "MISSING_FIELD:title",
            "SELECTOR_MISSING_PRICE",
            "CSS_SELECTOR_BROKEN",
            "XPATH_NOT_FOUND",
            "DOM_MUTATION_DETECTED",
            "DYNAMIC_CONTENT_FAILURE",
            "EXTRACTION_FAILED",
        ]
        for sig in signatures:
            result = self.classifier.classify(failure_signature=sig)
            assert result.category == FailureCategory.SCHEMA_EXTRACTION, f"Failed for {sig}"
            assert result.is_healable is True, f"Expected healable for {sig}"

    def test_schema_validation_failures_are_healable(self):
        """Verify EMPTY_FIELD, INVALID_FIELD, INVALID_TYPE, and parse errors classify as SCHEMA_VALIDATION and are healable."""
        signatures = [
            "EMPTY_FIELD:title",
            "INVALID_FIELD:price",
            "INVALID_TYPE:rating",
            "INVALID_VALUE:stock",
            "VALIDATION_RULE_FAILED",
            "TYPE_MISMATCH:in_stock",
            "PARSE_ERROR:date",
        ]
        for sig in signatures:
            result = self.classifier.classify(failure_signature=sig)
            assert result.category == FailureCategory.SCHEMA_VALIDATION, f"Failed for {sig}"
            assert result.is_healable is True, f"Expected healable for {sig}"

    def test_anomaly_failures_are_non_healable(self):
        """Verify historical anomaly signatures (price jump, count collapse) classify as ANOMALY and are non-healable."""
        signatures = [
            "ANOMALY:PRICE_JUMP",
            "ANOMALY:COUNT_COLLAPSE",
            "PRICE_JUMP_DETECTED",
            "COUNT_COLLAPSE_WARNING",
            "HISTORICAL_ANOMALY",
        ]
        for sig in signatures:
            result = self.classifier.classify(failure_signature=sig)
            assert result.category == FailureCategory.ANOMALY, f"Failed for {sig}"
            assert result.is_healable is False, f"Expected non-healable for {sig}"

    def test_infrastructure_failures_are_non_healable(self):
        """Verify CAPTCHA, rate limits, Cloudflare, and timeouts classify as INFRASTRUCTURE and are non-healable."""
        signatures = [
            "CAPTCHA_CHALLENGE",
            "CLOUDFLARE_CAPTCHA",
            "BOT_DETECTED",
            "HARD_BOT_DETECTION",
            "IP_RATE_LIMITED",
            "RATE_LIMIT_EXCEEDED",
            "PROXY_BLOCKED",
            "BLOCKED_403",
            "HTTP_403_FORBIDDEN",
            "HTTP_429_TOO_MANY_REQUESTS",
            "TIMEOUT_ERROR",
            "NETWORK_RESET",
        ]
        for sig in signatures:
            result = self.classifier.classify(failure_signature=sig)
            assert result.category == FailureCategory.INFRASTRUCTURE, f"Failed for {sig}"
            assert result.is_healable is False, f"Expected non-healable for {sig}"

    def test_structural_client_failures_are_non_healable(self):
        """Verify NO_EXPECTED_FIELDS, EMPTY_SCHEMA, and client errors classify as STRUCTURAL_CLIENT and are non-healable."""
        signatures = [
            "NO_EXPECTED_FIELDS",
            "EMPTY_SCHEMA_PROVIDED",
            "MALFORMED_SCHEMA",
            "CLIENT_ERROR_PAYLOAD",
            "CORRUPT_PAYLOAD",
        ]
        for sig in signatures:
            result = self.classifier.classify(failure_signature=sig)
            assert result.category == FailureCategory.STRUCTURAL_CLIENT, f"Failed for {sig}"
            assert result.is_healable is False, f"Expected non-healable for {sig}"

    def test_multi_tag_precedence_non_healable_overrides_healable(self):
        """Verify when multiple signatures are present, non-healable severity overrides healable tags."""
        # Combination: healable selector error + non-healable price jump anomaly
        combined_sig = "MISSING_FIELD:price, ANOMALY:PRICE_JUMP"
        result = self.classifier.classify(failure_signature=combined_sig)
        assert result.category == FailureCategory.ANOMALY
        assert result.is_healable is False

        # Combination: healable empty field + non-healable Cloudflare CAPTCHA
        combined_sig2 = "EMPTY_FIELD:title, CLOUDFLARE_CAPTCHA"
        result2 = self.classifier.classify(failure_signature=combined_sig2)
        assert result2.category == FailureCategory.INFRASTRUCTURE
        assert result2.is_healable is False

        # Combination: multiple healable tags remain healable
        combined_healable = "EMPTY_FIELD:title, INVALID_FIELD:price, MISSING_FIELD:rating"
        result3 = self.classifier.classify(failure_signature=combined_healable)
        assert result3.is_healable is True
        assert result3.category in HEALABLE_CATEGORIES

    def test_empty_none_and_unknown_failure_handling(self):
        """Verify None, empty, whitespace, and unknown tags are handled safely without crashing."""
        # None
        r_none = self.classifier.classify(failure_signature=None)
        assert r_none.category == FailureCategory.UNKNOWN
        assert r_none.is_healable is False

        # Empty string
        r_empty = self.classifier.classify(failure_signature="   ")
        assert r_empty.category == FailureCategory.UNKNOWN
        assert r_empty.is_healable is False

        # Completely unknown string
        r_unk = self.classifier.classify(failure_signature="XYZ_UNEXPECTED_UNSEEN_ERROR")
        assert r_unk.category == FailureCategory.UNKNOWN
        assert r_unk.is_healable is False

    def test_derivation_from_failed_fields_or_verification_result(self):
        """Verify classifier can derive signatures from failed_fields or verification_result when failure_signature is omitted."""
        # Derived from failed_fields
        r_fields = self.classifier.classify(failed_fields=["title", "price"])
        assert r_fields.category == FailureCategory.SCHEMA_EXTRACTION
        assert r_fields.is_healable is True
        assert "MISSING_FIELD:title" in r_fields.all_signatures

        # Derived from verification_result anomalies
        r_vr = self.classifier.classify(verification_result={"anomalies": ["ANOMALY:PRICE_JUMP"]})
        assert r_vr.category == FailureCategory.ANOMALY
        assert r_vr.is_healable is False


class TestSentinelDiagnoser:
    """Unit test suite for SentinelDiagnoser protocol compliance and context updates."""

    def test_diagnoser_updates_context_healability_and_metadata(self):
        """Verify diagnose() updates context.is_healable and attaches rich metadata."""
        diagnoser = SentinelDiagnoser()
        ctx = OrchestrationContext(
            url="https://example.com/test",
            failure_signature="MISSING_FIELD:price",
            status=OrchestratorState.DIAGNOSING,
        )

        diagnosed_ctx = diagnoser.diagnose(ctx)
        assert diagnosed_ctx.is_healable is True
        assert "diagnosis" in diagnosed_ctx.metadata
        assert diagnosed_ctx.metadata["diagnosis"]["category"] == "SCHEMA_EXTRACTION"
        assert diagnosed_ctx.metadata["diagnosis"]["is_healable"] is True
        assert "MISSING_FIELD:price" in diagnosed_ctx.metadata["diagnosis"]["signatures"]

    def test_diagnoser_preserves_existing_failure_signature(self):
        """Verify diagnose() preserves an existing valid failure signature."""
        diagnoser = SentinelDiagnoser()
        ctx = OrchestrationContext(
            url="https://example.com/test",
            failure_signature="ANOMALY:COUNT_COLLAPSE",
            status=OrchestratorState.DIAGNOSING,
        )

        diagnosed_ctx = diagnoser.diagnose(ctx)
        assert diagnosed_ctx.failure_signature == "ANOMALY:COUNT_COLLAPSE"
        assert diagnosed_ctx.is_healable is False
        assert diagnosed_ctx.metadata["diagnosis"]["category"] == "ANOMALY"


class TestDiagnoserStateTransitionsAndOrchestration:
    """Integration test suite ensuring SentinelDiagnoser works seamlessly with SentinelOrchestrator."""

    def test_healable_failure_transitions_to_healing(self):
        """Verify healable failure advances DIAGNOSING -> HEALING in state machine."""
        diagnoser = SentinelDiagnoser()
        orchestrator = SentinelOrchestrator(
            diagnoser=diagnoser,
            healer=StubHealer(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/item",
            status=OrchestratorState.DIAGNOSING,
            failure_signature="MISSING_FIELD:price",
            healing_attempts=0,
        )

        result = orchestrator.step(ctx)
        assert result.status == OrchestratorState.HEALING
        assert result.is_healable is True
        assert result.metadata["diagnosis"]["category"] == "SCHEMA_EXTRACTION"

    def test_non_healable_anomaly_bypasses_healing_and_escalates(self):
        """Verify non-healable anomaly bypasses HEALING and routes directly to ESCALATING."""
        diagnoser = SentinelDiagnoser()
        healer = StubHealer()
        escalator = StubEscalator(succeed=True)
        orchestrator = SentinelOrchestrator(
            diagnoser=diagnoser,
            healer=healer,
            escalator=escalator,
        )

        ctx = OrchestrationContext(
            url="https://example.com/item",
            status=OrchestratorState.DIAGNOSING,
            failure_signature="ANOMALY:PRICE_JUMP",
            healing_attempts=0,
            escalation_attempts=0,
        )

        result = orchestrator.step(ctx)
        assert result.status == OrchestratorState.ESCALATING
        assert result.is_healable is False
        assert healer.calls == 0  # Healer was bypassed

    def test_non_healable_infrastructure_blocks_when_escalation_exhausted(self):
        """Verify non-healable failure routes to BLOCKED when escalation attempts are exhausted."""
        diagnoser = SentinelDiagnoser()
        orchestrator = SentinelOrchestrator(
            diagnoser=diagnoser,
            escalator=StubEscalator(succeed=False),
        )

        ctx = OrchestrationContext(
            url="https://example.com/item",
            status=OrchestratorState.DIAGNOSING,
            failure_signature="HARD_BOT_DETECTION",
            escalation_attempts=1,  # max_escalation_attempts reached
        )

        result = orchestrator.step(ctx)
        assert result.status == OrchestratorState.BLOCKED
        assert result.is_healable is False

    def test_full_pipeline_with_real_validator_and_real_diagnoser(self):
        """Verify end-to-end pipeline using both real SentinelValidator and real SentinelDiagnoser."""
        validator = SentinelValidator()
        diagnoser = SentinelDiagnoser()

        class MissingFieldCollector:
            def __init__(self):
                self.calls = 0

            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                self.calls += 1
                if context.healing_attempts == 0:
                    context.extracted_data = {"title": "Test Item"}  # Missing price
                else:
                    context.extracted_data = {"title": "Test Item", "price": "$49.99"}
                return context

        orchestrator = SentinelOrchestrator(
            collector=MissingFieldCollector(),
            validator=validator,
            diagnoser=diagnoser,
            healer=StubHealer(success_on_attempt=1),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/pipeline-test",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)
        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.healing_attempts == 1
        assert result.failure_signature == "MISSING_FIELD:price"
        assert result.ai_answer is not None
        assert OrchestratorState.DIAGNOSING in result.state_history
        assert OrchestratorState.HEALING in result.state_history
