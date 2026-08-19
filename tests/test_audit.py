"""Comprehensive test suite for ExecutionAuditor, machine-readable audit records, and secret redaction."""

from unittest.mock import MagicMock
import pytest

from app.audit.auditor import ExecutionAuditor
from app.audit.models import ExecutionAudit
from app.audit.sanitizer import sanitize_audit_data, sanitize_string, sanitize_url
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
    StubValidator,
)
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator


class TestExecutionAuditAndObservability:
    """Test suite verifying audit record construction, deterministic telemetry, and secret protection."""

    def test_successful_execution_audit(self):
        """Verify successful pipeline produces complete, machine-readable audit record."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=True,
            status_code=200,
            data={"title": "Mechanical Keyboard", "price": 149.0, "rating": 4.9},
            response_time_ms=125.0,
            tier_used="scraping_browser",
        )

        orchestrator = SentinelOrchestrator(
            collector=BrightDataCollector(client=mock_client),
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
            enable_cost_tracking=True,
        )

        ctx = OrchestrationContext(
            collector_id="req_001",
            url="https://example.com/keyboard",
            schema={"title": "string", "price": "price", "rating": "rating"},
        )

        result = orchestrator.run(ctx)

        assert "audit_record" in result.metadata
        audit = result.metadata["audit_record"]

        assert audit["request_id"] == "req_001"
        assert audit["url"] == "https://example.com/keyboard"
        assert audit["final_status"] == "AI_READY"
        assert audit["is_verified"] is True
        assert audit["trust_score"] == 100.0
        assert audit["validation_summary"]["passed"] is True
        assert audit["validation_summary"]["fields_expected"] == 3
        assert audit["validation_summary"]["fields_valid"] == 3
        assert audit["budget_summary"]["total_cost_usd"] > 0.0
        assert audit["timing_ms"]["collector_response_ms"] == 125.0
        assert "START" in audit["state_transitions"]
        assert "AI_READY" in audit["state_transitions"]

    def test_validation_failure_audit(self):
        """Verify validation rejection produces detailed audit record with failed fields and signature."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"title": "Item Without Price"}),
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/invalid-item",
            schema={
                "title": {"type": "string", "required": True},
                "price": {"type": "price", "required": True},
            },
            max_healing_attempts=0,  # Immediately block
            max_escalation_attempts=0,
        )

        result = orchestrator.run(ctx)

        audit = result.metadata["audit_record"]
        assert audit["final_status"] == "BLOCKED"
        assert audit["is_verified"] is False
        assert "price" in audit["failed_fields"]
        assert "MISSING_FIELD:price" in audit["failure_signature"]
        assert audit["validation_summary"]["passed"] is False

    def test_healing_audit_recording(self):
        """Verify healing attempts and strategies are captured in audit record."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            # Attempt 0: Missing field
            BrightDataResponse(success=True, status_code=200, data={"title": "Monitor"}),
            # Attempt 1: Recovered
            BrightDataResponse(success=True, status_code=200, data={"title": "Monitor", "price": 299.0}),
        ]

        orchestrator = SentinelOrchestrator(
            collector=BrightDataCollector(client=mock_client),
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/monitor",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)
        audit = result.metadata["audit_record"]

        assert len(audit["healing_attempts"]) == 1
        assert audit["healing_attempts"][0]["attempt"] == 1
        assert audit["healing_attempts"][0]["strategy"] == "CSS_SELECTOR_FALLBACK"

    def test_escalation_audit_recording(self):
        """Verify tier escalation reasons and transitions are captured in audit record."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.side_effect = [
            # Attempt 0: Anti-bot block
            BrightDataResponse(success=False, status_code=403, error="HTTP_403_FORBIDDEN"),
            # Attempt 1: Escalated tier succeeds
            BrightDataResponse(success=True, status_code=200, data={"title": "Protected Item", "price": 50.0}),
        ]

        orchestrator = SentinelOrchestrator(
            collector=BrightDataCollector(client=mock_client),
            validator=SentinelValidator(),
            diagnoser=SentinelDiagnoser(),
            healer=SentinelHealer(),
            escalator=SentinelEscalator(),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/protected-bot",
            schema={"title": "string", "price": "price"},
        )

        result = orchestrator.run(ctx)
        audit = result.metadata["audit_record"]

        assert len(audit["escalation_attempts"]) == 1
        assert audit["escalation_attempts"][0]["tier"] == "unblocker_browser"
        assert "HTTP_403_FORBIDDEN" in audit["escalation_attempts"][0]["reason"]

    def test_budget_blocked_audit(self):
        """Verify budget cap violations are cleanly reflected in budget_summary and failure_signature."""
        guard = BudgetGuard(config=BudgetConfig(max_budget_usd=0.001))
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(),
            validator=SentinelValidator(),
            budget_guard=guard,
        )

        ctx = OrchestrationContext(
            url="https://example.com/expensive",
            compute_tier="scraping_browser",  # Costs $0.015 > $0.001 cap
        )

        result = orchestrator.run(ctx)
        audit = result.metadata["audit_record"]

        assert audit["final_status"] == "BLOCKED"
        assert audit["budget_summary"]["budget_exceeded"] is True
        assert "BUDGET_EXCEEDED:USD_LIMIT" in audit["failure_signature"]

    def test_final_failure_audit(self):
        """Verify unexpected exceptions generate audit record with FAILED status."""
        class CrashingValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                raise RuntimeError("Unexpected DB failure in validator")

        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"item": "test"}),
            validator=CrashingValidator(),
        )

        ctx = OrchestrationContext(url="https://example.com/crash-test")
        result = orchestrator.run(ctx)
        audit = result.metadata["audit_record"]

        assert audit["final_status"] == "FAILED"
        assert "Unexpected DB failure" in audit["metadata"].get("error", "")

    def test_state_transition_recording(self):
        """Verify state transitions sequence is accurately recorded in order."""
        orchestrator = SentinelOrchestrator(
            collector=StubCollector(data_to_return={"title": "Item"}),
            validator=SentinelValidator(),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/states",
            schema={"title": "string"},
        )

        result = orchestrator.run(ctx)
        audit = result.metadata["audit_record"]

        expected_transitions = ["START", "COLLECTING", "VALIDATING", "TRUST_GATE", "VERIFIED", "AI_READY"]
        for expected in expected_transitions:
            assert expected in audit["state_transitions"]

    def test_secret_and_credential_redaction(self):
        """Verify sensitive credentials, auth headers, passwords, and URLs are masked."""
        # 1. URL with credentials
        raw_url = "wss://brd-customer-hl_12345-zone-scraping1:secretpass99@brd.superproxy.io:9222"
        sanitized_url = sanitize_url(raw_url)
        assert "secretpass99" not in sanitized_url
        assert "wss://***:***@brd.superproxy.io:9222" in sanitized_url

        # 2. String with Bearer token
        raw_str = "Authorization: Bearer my_secret_token_12345"
        sanitized_str = sanitize_string(raw_str)
        assert "my_secret_token_12345" not in sanitized_str
        assert "Bearer ***" in sanitized_str

        # 3. Dict with sensitive keys
        data = {
            "api_key": "sec_abc123",
            "bright_data_password": "super_secret_password",
            "nested": {
                "auth_token": "token_xyz",
                "normal_field": "safe_value",
            },
        }
        sanitized_dict = sanitize_audit_data(data)
        assert sanitized_dict["api_key"] == "***REDACTED***"
        assert sanitized_dict["bright_data_password"] == "***REDACTED***"
        assert sanitized_dict["nested"]["normal_field"] == "safe_value"

    def test_deterministic_audit_structure(self):
        """Verify ExecutionAudit conforms to pydantic model and serializes cleanly."""
        ctx = OrchestrationContext(
            collector_id="test_req",
            url="https://example.com/audit",
            status=OrchestratorState.AI_READY,
            trust_score=100.0,
            state_history=[OrchestratorState.START, OrchestratorState.COLLECTING, OrchestratorState.AI_READY],
        )

        audit = ExecutionAuditor.build_audit_record(ctx)
        assert isinstance(audit, ExecutionAudit)
        dumped = audit.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["request_id"] == "test_req"
        assert dumped["final_status"] == "AI_READY"
