"""Unit and integration tests for Bright Data client and collector adapter."""

import os
from unittest.mock import MagicMock, patch
import httpx
import pytest

from app.integrations.brightdata.client import (
    BrightDataClient,
    BrightDataConfig,
    BrightDataConfigError,
    BrightDataResponse,
)
from app.integrations.brightdata.collector import BrightDataCollector
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import Collector, StubAIService, StubValidator
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState


class TestBrightDataConfigAndSecurity:
    """Tests verifying configuration validation and secret masking."""

    def test_missing_configuration_detected(self):
        """Verify client recognizes missing API key and fails safely."""
        config = BrightDataConfig(api_key=None)
        assert config.is_configured() is False

        client = BrightDataClient(config=config)
        with pytest.raises(BrightDataConfigError, match="BRIGHT_DATA_API_KEY environment variable"):
            client.validate_config()

        # Scraping without config returns safe error response without crashing
        response = client.scrape(url="https://example.com/test")
        assert response.success is False
        assert "CONFIG_ERROR" in (response.error or "")

    def test_secrets_not_exposed_in_repr_or_str(self):
        """Verify API key is masked in string representation and not leaked."""
        secret_key = "super_secret_brightdata_key_12345"
        config = BrightDataConfig(api_key=secret_key, zone="custom_zone")

        repr_str = repr(config)
        str_str = str(config)

        assert secret_key not in repr_str
        assert secret_key not in str_str
        assert "***" in repr_str

    def test_env_var_configuration_loading(self, monkeypatch):
        """Verify configuration correctly loads from environment variables."""
        monkeypatch.setenv("BRIGHT_DATA_API_KEY", "test_env_key_abc")
        monkeypatch.setenv("BRIGHT_DATA_ZONE", "test_zone_xyz")
        monkeypatch.setenv("BRIGHT_DATA_BASE_URL", "https://custom.brightdata.endpoint")

        config = BrightDataConfig()
        assert config.api_key == "test_env_key_abc"
        assert config.zone == "test_zone_xyz"
        assert config.base_url == "https://custom.brightdata.endpoint"
        assert config.is_configured() is True


class TestBrightDataClientScraping:
    """Tests verifying mocked HTTP execution and error handling."""

    def setup_method(self):
        self.config = BrightDataConfig(api_key="valid_dummy_key", zone="web_unlocker")
        self.client = BrightDataClient(config=self.config)

    @patch("httpx.Client.post")
    def test_successful_mocked_scrape(self, mock_post):
        """Verify successful response parsing and data extraction."""
        mock_post.return_value = httpx.Response(
            status_code=200,
            json={"data": {"title": "Noise-Cancelling Headphones", "price": "$199.99"}},
            request=httpx.Request("POST", "https://api.brightdata.com/request"),
        )

        response = self.client.scrape(
            url="https://example.com/headphones",
            tier="standard",
            schema={"title": "string", "price": "price"},
        )

        assert response.success is True
        assert response.status_code == 200
        assert response.data == {"title": "Noise-Cancelling Headphones", "price": "$199.99"}
        assert response.error is None
        assert response.response_time_ms >= 0.0

    @patch("httpx.Client.post")
    def test_http_403_forbidden_handling(self, mock_post):
        """Verify HTTP 403 maps to safe failure message."""
        mock_post.return_value = httpx.Response(
            status_code=403,
            text="Forbidden",
            request=httpx.Request("POST", "https://api.brightdata.com/request"),
        )

        response = self.client.scrape(url="https://example.com/protected")
        assert response.success is False
        assert response.status_code == 403
        assert response.error == "HTTP_403_FORBIDDEN"

    @patch("httpx.Client.post")
    def test_http_429_rate_limit_handling(self, mock_post):
        """Verify HTTP 429 maps to rate limit error message."""
        mock_post.return_value = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            request=httpx.Request("POST", "https://api.brightdata.com/request"),
        )

        response = self.client.scrape(url="https://example.com/rate-limited")
        assert response.success is False
        assert response.status_code == 429
        assert response.error == "HTTP_429_TOO_MANY_REQUESTS"

    @patch("httpx.Client.post")
    def test_timeout_handling(self, mock_post):
        """Verify network timeout returns clean 504 error response."""
        mock_post.side_effect = httpx.TimeoutException("Read timed out")

        response = self.client.scrape(url="https://example.com/timeout")
        assert response.success is False
        assert response.status_code == 504
        assert "TIMEOUT_ERROR" in (response.error or "")

    @patch("httpx.Client.post")
    def test_network_connection_error_handling(self, mock_post):
        """Verify network disconnect returns clean 502 error response."""
        mock_post.side_effect = httpx.ConnectError("Failed to resolve host")

        response = self.client.scrape(url="https://example.com/network-error")
        assert response.success is False
        assert response.status_code == 502
        assert "NETWORK_ERROR" in (response.error or "")


class TestBrightDataCollectorAdapter:
    """Tests verifying BrightDataCollector integration with OrchestrationContext and State Machine."""

    def test_collector_protocol_conformance(self):
        """Verify BrightDataCollector conforms to Collector runtime protocol."""
        collector = BrightDataCollector()
        assert isinstance(collector, Collector)

    def test_collector_updates_context_on_success(self):
        """Verify collect() populates extracted_data and metadata on success."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=True,
            status_code=200,
            data={"title": "Keyboard", "price": "$129"},
            tier_used="standard",
            response_time_ms=120.5,
        )

        collector = BrightDataCollector(client=mock_client)
        ctx = OrchestrationContext(url="https://example.com/product", compute_tier="standard")

        result_ctx = collector.collect(ctx)
        assert result_ctx.extracted_data == {"title": "Keyboard", "price": "$129"}
        assert "brightdata" in result_ctx.metadata
        assert result_ctx.metadata["brightdata"]["status_code"] == 200

    def test_collector_handles_error_and_assigns_failure_signature(self):
        """Verify collect() handles scrape failure and assigns failure signature."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=False,
            status_code=403,
            error="HTTP_403_FORBIDDEN",
            tier_used="standard",
        )

        collector = BrightDataCollector(client=mock_client)
        ctx = OrchestrationContext(url="https://example.com/blocked")

        result_ctx = collector.collect(ctx)
        assert result_ctx.extracted_data is None
        assert result_ctx.metadata.get("brightdata_error") == "HTTP_403_FORBIDDEN"
        assert result_ctx.failure_signature == "HTTP_403_FORBIDDEN"

    def test_orchestrator_integration_with_mocked_brightdata_collector(self):
        """Verify SentinelOrchestrator runs full pipeline using BrightDataCollector."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=True,
            status_code=200,
            data={"title": "Ultra-Wide Monitor", "price": "$499.00"},
            tier_used="standard",
        )

        collector = BrightDataCollector(client=mock_client)
        orchestrator = SentinelOrchestrator(
            collector=collector,
            validator=StubValidator(default_passed=True),
            ai_service=StubAIService(),
        )

        ctx = OrchestrationContext(
            url="https://example.com/monitor",
            schema={"title": "string", "price": "price"},
        )
        result = orchestrator.run(ctx)

        assert result.status == OrchestratorState.AI_READY
        assert result.is_verified() is True
        assert result.extracted_data == {"title": "Ultra-Wide Monitor", "price": "$499.00"}
        assert result.ai_answer is not None
