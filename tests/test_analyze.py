"""Tests for POST /analyze endpoint integrating State Machine and SentinelValidator."""

import pytest
from fastapi.testclient import TestClient

from app.api.analyze import get_orchestrator
from app.main import app
from app.models.response import SentinelResponse
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import StubCollector, StubValidator
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from app.validation.validator import SentinelValidator

client = TestClient(app)


class TestAnalyzeEndpoint:
    """Test suite for POST /analyze endpoint and full lifecycle integration."""

    @pytest.fixture(autouse=True)
    def clean_baseline_store(self):
        """Ensure baseline store is pristine for every test run (Priority 4 Test Isolation)."""
        from app.api.analyze import _shared_validator
        _shared_validator.baseline_store.clear()
        yield
        _shared_validator.baseline_store.clear()

    def test_analyze_successful_flow(self):
        """Verify successful POST /analyze completes full pipeline and reaches AI_READY."""
        payload = {
            "url": "https://example.com/item/100",
            "schema": {
                "title": {"type": "string", "required": True},
                "price": {"type": "price", "required": True},
            },
            "prompt": "Summarize verified item",
        }

        response = client.post("/analyze", json=payload)
        assert response.status_code == 200
        data = response.json()

        # Contract checks
        assert data["url"] == "https://example.com/item/100"
        assert data["status"] == "AI_READY"
        assert data["trust_score"] == 100.0
        assert data["verification_result"] is not None
        assert data["verification_result"]["passed"] is True
        assert data["ai_answer"] is not None
        assert "Summarize verified item" in data["ai_answer"]
        assert data["failed_fields"] == []

    def test_analyze_validator_rejection_on_invalid_data(self):
        """Verify validator failure stops pipeline at failure path with failed_fields and signature."""
        invalid_collector = StubCollector(
            data_to_return={
                "title": "",  # Empty string
                "price": "not_a_price",  # Invalid price
                # missing rating
            }
        )
        app.dependency_overrides[get_orchestrator] = lambda: SentinelOrchestrator(
            collector=invalid_collector,
            validator=SentinelValidator(),
        )
        try:
            payload = {
                "url": "https://example.com/item/invalid",
                "schema": {
                    "title": {"type": "non-empty string", "required": True},
                    "price": {"type": "price", "required": True},
                    "rating": {"type": "rating", "required": True},
                },
            }

            response = client.post("/analyze", json=payload)
            assert response.status_code == 200
            data = response.json()

            assert data["status"] == "BLOCKED"
            assert data["status"] != "AI_READY"
            assert data["trust_score"] < 100.0
            assert data["verification_result"]["passed"] is False
            assert "rating" in data["failed_fields"]
            assert "MISSING_FIELD:rating" in data["failure_signature"]
            assert "EMPTY_FIELD:title" in data["failure_signature"]
            assert "INVALID_FIELD:price" in data["failure_signature"]
            assert data["ai_answer"] is None
        finally:
            app.dependency_overrides.clear()

    def test_analyze_trust_gate_blocks_unverified_data(self):
        """Verify Trust Gate prevents unverified data from advancing to AI stage."""
        incomplete_collector = StubCollector(
            data_to_return={"title": "Item Without SKU"}  # missing required sku
        )
        app.dependency_overrides[get_orchestrator] = lambda: SentinelOrchestrator(
            collector=incomplete_collector,
            validator=SentinelValidator(),
        )
        try:
            payload = {
                "url": "https://example.com/item/missing-fields",
                "schema": {
                    "title": {"type": "string", "required": True},
                    "sku": {"type": "string", "required": True},
                },
            }

            response = client.post("/analyze", json=payload)
            assert response.status_code == 200
            data = response.json()

            assert data["status"] != "AI_READY"
            assert data["status"] == "BLOCKED"
            assert data["verification_result"]["passed"] is False
            assert "sku" in data["failed_fields"]
            assert data["ai_answer"] is None
        finally:
            app.dependency_overrides.clear()

    def test_analyze_validation_empty_url_returns_422(self):
        """Verify missing or empty URL returns HTTP 422 Unprocessable Entity."""
        response = client.post("/analyze", json={"url": "   "})
        assert response.status_code == 422

        response_no_url = client.post("/analyze", json={})
        assert response_no_url.status_code == 422

    def test_analyze_confirms_orchestrator_and_validator_invoked(self):
        """Verify dependency injection confirms orchestrator and real validator are executed."""
        calls = {"orchestrator_run": 0, "validator_called": 0}

        class MockValidator:
            def validate(self, context: OrchestrationContext) -> OrchestrationContext:
                calls["validator_called"] += 1
                context.trust_score = 100.0
                context.verification_result = {"passed": True}
                return context

        mock_validator = MockValidator()
        mock_orchestrator = SentinelOrchestrator(validator=mock_validator)

        def override_get_orchestrator():
            calls["orchestrator_run"] += 1
            return mock_orchestrator

        app.dependency_overrides[get_orchestrator] = override_get_orchestrator
        try:
            response = client.post("/analyze", json={"url": "https://example.com/override"})
            assert response.status_code == 200
            assert calls["orchestrator_run"] == 1
            assert calls["validator_called"] >= 1
            data = response.json()
            assert data["status"] == "AI_READY"
        finally:
            app.dependency_overrides.clear()

    def test_analyze_handles_orchestrator_exceptions_gracefully(self):
        """Verify unhandled exceptions in state machine are caught and serialized into response."""
        class CrashingCollector:
            def collect(self, context: OrchestrationContext) -> OrchestrationContext:
                raise RuntimeError("Catastrophic network partition")

        crashing_orchestrator = SentinelOrchestrator(
            collector=CrashingCollector(),
            validator=SentinelValidator(),
        )

        app.dependency_overrides[get_orchestrator] = lambda: crashing_orchestrator
        try:
            response = client.post("/analyze", json={"url": "https://example.com/crash"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FAILED"
            assert data["ai_answer"] is None
        finally:
            app.dependency_overrides.clear()

    def test_unexpected_orchestration_failure_produces_failed_status(self):
        """Regression test (Priority 2): Verify unhandled exception in orchestrator.run() produces deterministic FAILED status."""
        class ExplodingOrchestrator:
            def run(self, context: OrchestrationContext, max_steps: int = 50) -> OrchestrationContext:
                context.status = OrchestratorState.VALIDATING
                raise RuntimeError("Unexpected orchestrator crash in middle of pipeline")

        app.dependency_overrides[get_orchestrator] = lambda: ExplodingOrchestrator()
        try:
            response = client.post("/analyze", json={"url": "https://example.com/exploding"})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "FAILED"
            assert data["status"] != "VALIDATING"
            assert data["status"] != "COLLECTING"
            assert data["ai_answer"] is None
        finally:
            app.dependency_overrides.clear()

    def test_client_supplied_extracted_data_cannot_bypass_collector(self):
        """Regression test (Priority 1): Prove client-supplied extracted_data cannot bypass collector/provenance."""
        real_collector_data = {"title": "Real Scraped Item", "price": "$29.99"}
        app.dependency_overrides[get_orchestrator] = lambda: SentinelOrchestrator(
            collector=StubCollector(data_to_return=real_collector_data),
            validator=SentinelValidator(),
        )
        try:
            payload = {
                "url": "https://example.com/item/tamper-attempt",
                "schema": {
                    "title": {"type": "string", "required": True},
                    "price": {"type": "price", "required": True},
                },
                "extracted_data": {"title": "Fabricated bypass", "price": "$0.01"},
            }
            response = client.post("/analyze", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert data["extracted_data"]["title"] == "Real Scraped Item"
            assert data["extracted_data"]["price"] == "$29.99"
        finally:
            app.dependency_overrides.clear()

    def test_analyze_baseline_and_historical_anomaly_detection(self):
        """Verify historical baseline and anomaly detection works across multiple POST /analyze requests."""
        key_url = "https://example.com/dynamic-price-product"
        schema = {"title": "string", "price": "price"}

        shared_val = SentinelValidator()
        collector1 = StubCollector(data_to_return={"title": "Smart Watch", "price": 100.0})
        app.dependency_overrides[get_orchestrator] = lambda: SentinelOrchestrator(
            collector=collector1,
            validator=shared_val,
        )

        try:
            # 1. Baseline Request: price = 100
            resp1 = client.post(
                "/analyze",
                json={
                    "url": key_url,
                    "schema": schema,
                },
            )
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1["status"] == "AI_READY"
            assert data1["verification_result"]["is_baseline"] is True

            # 2. Suspicious Price Jump Request: price = 195 (> 50% increase)
            collector2 = StubCollector(data_to_return={"title": "Smart Watch", "price": 195.0})
            app.dependency_overrides[get_orchestrator] = lambda: SentinelOrchestrator(
                collector=collector2,
                validator=shared_val,
            )

            resp2 = client.post(
                "/analyze",
                json={
                    "url": key_url,
                    "schema": schema,
                },
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["status"] != "AI_READY"
            assert data2["verification_result"]["passed"] is False
            assert "ANOMALY:PRICE_JUMP" in data2["failure_signature"]
        finally:
            app.dependency_overrides.clear()
