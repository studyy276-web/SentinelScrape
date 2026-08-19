"""Unit and integration tests for Bright Data Browser API client and collector adapter."""

import os
from unittest.mock import MagicMock, patch
import pytest

from app.integrations.brightdata.client import (
    BrightDataClient,
    BrightDataConfig,
    BrightDataConfigError,
    BrightDataResponse,
    _sanitize_error_message,
)
from app.integrations.brightdata.collector import BrightDataCollector
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.interfaces import Collector, StubAIService, StubValidator
from app.orchestrator.state_machine import SentinelOrchestrator
from app.orchestrator.states import OrchestratorState
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError


class TestBrightDataConfigAndSecurity:
    """Tests verifying Browser API configuration validation and secret masking."""

    def test_missing_configuration_detected(self):
        """Verify client recognizes missing API key / WS URL and fails safely."""
        config = BrightDataConfig(api_key=None, ws_url=None)
        assert config.is_configured() is False

        client = BrightDataClient(config=config)
        with pytest.raises(BrightDataConfigError, match="credentials are missing"):
            client.validate_config()

        # Scraping without config returns safe error response without crashing
        response = client.scrape(url="https://example.com/test")
        assert response.success is False
        assert "CONFIG_ERROR" in (response.error or "")

    def test_secrets_not_exposed_in_repr_or_str(self):
        """Verify API key and credentials are masked in string representation and error messages."""
        secret_key = "super_secret_browser_key_12345"
        config = BrightDataConfig(api_key=secret_key, zone="scraping_browser1")

        repr_str = repr(config)
        str_str = str(config)

        assert secret_key not in repr_str
        assert secret_key not in str_str
        assert "***" in repr_str

        # Verify error sanitization masks URLs containing credentials
        err_msg = f"Failed to connect to wss://user:{secret_key}@brd.superproxy.io:9222"
        sanitized = _sanitize_error_message(err_msg, secret=secret_key)
        assert secret_key not in sanitized
        assert "wss://***:***@" in sanitized

    def test_env_var_configuration_loading(self, monkeypatch):
        """Verify configuration correctly loads Browser API settings from environment variables."""
        monkeypatch.setenv("BRIGHT_DATA_API_KEY", "test_browser_key_abc")
        monkeypatch.setenv("BRIGHT_DATA_ZONE", "scraping_browser1")
        monkeypatch.setenv("BRIGHT_DATA_BROWSER_HOST", "custom.superproxy.io")
        monkeypatch.setenv("BRIGHT_DATA_BROWSER_PORT", "9223")

        config = BrightDataConfig()
        assert config.api_key == "test_browser_key_abc"
        assert config.zone == "scraping_browser1"
        assert config.browser_host == "custom.superproxy.io"
        assert config.browser_port == 9223
        assert config.is_configured() is True
        assert config.get_ws_endpoint() == "wss://brd-customer-zone-scraping_browser1:test_browser_key_abc@custom.superproxy.io:9223"


class TestBrightDataBrowserClientScraping:
    """Tests verifying mocked Playwright CDP WebSocket execution and error handling."""

    def setup_method(self):
        self.config = BrightDataConfig(api_key="valid_dummy_key", zone="scraping_browser1")
        self.client = BrightDataClient(config=self.config)

    @patch("app.integrations.brightdata.client.sync_playwright")
    def test_successful_mocked_html_scrape(self, mock_sync_playwright):
        """Verify successful Playwright browser navigation and document extraction."""
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p

        mock_browser = MagicMock()
        mock_p.chromium.connect_over_cdp.return_value = mock_browser

        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_page.content.return_value = "<html><body><h1>Test Page</h1></body></html>"
        mock_page.title.return_value = "Test Title"
        mock_page.inner_text.return_value = "Test Page Text"

        response = self.client.scrape(
            url="https://example.com/product",
            tier="scraping_browser",
        )

        assert response.success is True
        assert response.status_code == 200
        assert response.data == {
            "title": "Test Title",
            "content": "<html><body><h1>Test Page</h1></body></html>",
        }
        assert response.raw_content == "<html><body><h1>Test Page</h1></body></html>"
        assert response.error is None
        assert response.response_time_ms >= 0.0
        assert mock_browser.close.called

    @patch("app.integrations.brightdata.client.sync_playwright")
    def test_successful_mocked_json_endpoint_scrape(self, mock_sync_playwright):
        """Verify JSON endpoints parsed into dictionary from inner_text."""
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p

        mock_browser = MagicMock()
        mock_p.chromium.connect_over_cdp.return_value = mock_browser

        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_page.content.return_value = '<html><body>{"ip": "1.2.3.4", "country": "US"}</body></html>'
        mock_page.title.return_value = "lumtest"
        mock_page.inner_text.return_value = '{"ip": "1.2.3.4", "country": "US"}'

        response = self.client.scrape(url="https://lumtest.com/myip.json")

        assert response.success is True
        assert response.status_code == 200
        assert response.data == {"ip": "1.2.3.4", "country": "US"}

    @patch("app.integrations.brightdata.client.sync_playwright")
    def test_playwright_timeout_error_handling(self, mock_sync_playwright):
        """Verify PlaywrightTimeoutError maps to 504 TIMEOUT_ERROR."""
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_p.chromium.connect_over_cdp.side_effect = PlaywrightTimeoutError("Navigation timeout of 30000ms exceeded")

        response = self.client.scrape(url="https://example.com/slow")
        assert response.success is False
        assert response.status_code == 504
        assert "TIMEOUT_ERROR" in (response.error or "")

    @patch("app.integrations.brightdata.client.sync_playwright")
    def test_playwright_403_forbidden_handling(self, mock_sync_playwright):
        """Verify 403 Forbidden / Unauthorized Playwright error maps to HTTP_403_FORBIDDEN."""
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_p.chromium.connect_over_cdp.side_effect = PlaywrightError("403 Forbidden: Invalid credentials")

        response = self.client.scrape(url="https://example.com/forbidden")
        assert response.success is False
        assert response.status_code == 403
        assert "HTTP_403_FORBIDDEN" in (response.error or "")

    @patch("app.integrations.brightdata.client.sync_playwright")
    def test_playwright_429_rate_limit_handling(self, mock_sync_playwright):
        """Verify 429 Too Many Requests Playwright error maps to HTTP_429_TOO_MANY_REQUESTS."""
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_p.chromium.connect_over_cdp.side_effect = PlaywrightError("429 Too Many Requests")

        response = self.client.scrape(url="https://example.com/rate-limited")
        assert response.success is False
        assert response.status_code == 429
        assert "HTTP_429_TOO_MANY_REQUESTS" in (response.error or "")

    @patch("app.integrations.brightdata.client.sync_playwright")
    def test_playwright_network_websocket_error_handling(self, mock_sync_playwright):
        """Verify WebSocket / Connection refused error maps to NETWORK_ERROR."""
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        mock_p.chromium.connect_over_cdp.side_effect = PlaywrightError("WebSocket connection to brd.superproxy.io failed: Connection refused")

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
            data={"title": "Keyboard", "content": "<div>$129</div>"},
            tier_used="scraping_browser",
            response_time_ms=120.5,
        )

        collector = BrightDataCollector(client=mock_client)
        ctx = OrchestrationContext(url="https://example.com/product", compute_tier="scraping_browser")

        result_ctx = collector.collect(ctx)
        assert result_ctx.extracted_data == {"title": "Keyboard", "content": "<div>$129</div>"}
        assert "brightdata" in result_ctx.metadata
        assert result_ctx.metadata["brightdata"]["status_code"] == 200

    def test_collector_handles_error_and_assigns_failure_signature(self):
        """Verify collect() handles scrape failure and assigns failure signature."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=False,
            status_code=403,
            error="HTTP_403_FORBIDDEN: Access denied",
            tier_used="scraping_browser",
        )

        collector = BrightDataCollector(client=mock_client)
        ctx = OrchestrationContext(url="https://example.com/blocked")

        result_ctx = collector.collect(ctx)
        assert result_ctx.extracted_data is None
        assert "brightdata_error" in result_ctx.metadata
        assert result_ctx.failure_signature == "HTTP_403_FORBIDDEN"

    def test_orchestrator_integration_with_mocked_brightdata_collector(self):
        """Verify SentinelOrchestrator runs full pipeline using BrightDataCollector."""
        mock_client = MagicMock(spec=BrightDataClient)
        mock_client.scrape.return_value = BrightDataResponse(
            success=True,
            status_code=200,
            data={"title": "Ultra-Wide Monitor", "price": "$499.00"},
            tier_used="scraping_browser",
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
