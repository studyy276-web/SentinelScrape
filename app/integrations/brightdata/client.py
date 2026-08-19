"""Bright Data Browser API client using Playwright CDP WebSocket connection."""

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field

try:
    from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
    PlaywrightError = Exception
    PlaywrightTimeoutError = Exception

logger = logging.getLogger(__name__)


class BrightDataError(Exception):
    """Base exception for Bright Data integration errors."""
    pass


class BrightDataConfigError(BrightDataError):
    """Raised when Bright Data credentials or configuration are missing or invalid."""
    pass


class BrightDataAPIError(BrightDataError):
    """Raised when Bright Data API returns an unhandled error response."""
    pass


class BrightDataConfig(BaseModel):
    """Configuration settings for Bright Data Browser API integration."""

    model_config = ConfigDict(populate_by_name=True)

    api_key: Optional[str] = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_API_KEY"))
    customer_id: Optional[str] = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_CUSTOMER_ID"))
    password: Optional[str] = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_PASSWORD"))
    auth_user: Optional[str] = Field(
        default_factory=lambda: os.getenv("BRIGHT_DATA_AUTH") or os.getenv("BRIGHT_DATA_USERNAME")
    )
    zone: str = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_ZONE", "sentinelscrape_browser"))
    browser_host: str = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_BROWSER_HOST", "brd.superproxy.io"))
    browser_port: int = Field(default_factory=lambda: int(os.getenv("BRIGHT_DATA_BROWSER_PORT", "9222")))
    base_url: str = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_BASE_URL", "https://api.brightdata.com"))
    ws_url: Optional[str] = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_WS_URL"))
    timeout_seconds: float = 30.0

    def is_configured(self) -> bool:
        """Returns True if a valid API key or WebSocket URL is present."""
        return bool(
            (self.api_key and self.api_key.strip())
            or (self.ws_url and self.ws_url.strip())
        )

    def resolve_credentials(self) -> None:
        """Auto-resolves customer ID and zone password via Bright Data REST API if not explicitly set."""
        if self.ws_url or not self.api_key:
            return

        if self.customer_id and self.password:
            return

        try:
            headers = {"Authorization": f"Bearer {self.api_key.strip()}"}
            with httpx.Client(timeout=10.0) as client:
                # 1. Resolve customer ID if missing
                if not self.customer_id:
                    res_status = client.get(f"{self.base_url.rstrip('/')}/status", headers=headers)
                    if res_status.status_code == 200:
                        status_data = res_status.json()
                        self.customer_id = status_data.get("customer")

                # 2. Resolve zone password if missing
                if not self.password and self.zone:
                    res_zone = client.get(
                        f"{self.base_url.rstrip('/')}/zone",
                        params={"zone": self.zone},
                        headers=headers,
                    )
                    if res_zone.status_code == 200:
                        zone_data = res_zone.json()
                        pwd = zone_data.get("password")
                        if isinstance(pwd, list) and pwd:
                            self.password = str(pwd[0])
                        elif pwd:
                            self.password = str(pwd)
        except Exception as e:
            logger.debug("Failed to auto-resolve Bright Data credentials: %s", e)

    def get_ws_endpoint(self) -> str:
        """Constructs the authenticated WebSocket endpoint for Bright Data Browser API."""
        if self.ws_url and self.ws_url.strip():
            return self.ws_url.strip()

        self.resolve_credentials()

        key = (self.password or self.api_key or "").strip()
        if self.auth_user and self.auth_user.strip():
            user = self.auth_user.strip()
        elif self.customer_id and self.customer_id.strip():
            user = f"brd-customer-{self.customer_id.strip()}-zone-{self.zone}"
        else:
            user = f"brd-customer-zone-{self.zone}"

        return f"wss://{user}:{key}@{self.browser_host}:{self.browser_port}"

    def __repr__(self) -> str:
        """Masks sensitive credentials in string representation."""
        masked_key = "***" if self.api_key else "None"
        return (
            f"BrightDataConfig(zone='{self.zone}', host='{self.browser_host}:{self.browser_port}', "
            f"api_key='{masked_key}')"
        )

    def __str__(self) -> str:
        return self.__repr__()


def _sanitize_error_message(msg: str, secret: Optional[str] = None) -> str:
    """Removes sensitive keys, passwords, or full WebSocket URLs from error messages."""
    sanitized = msg
    if secret and secret in sanitized:
        sanitized = sanitized.replace(secret, "***")
    sanitized = re.sub(r"wss://[^@]+@", "wss://***:***@", sanitized)
    sanitized = re.sub(r"brd-customer-[^:]+:[^@]+@", "brd-customer-***:***@", sanitized)
    return sanitized


class BrightDataResponse(BaseModel):
    """Normalized response from Bright Data Browser scraping execution."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    status_code: int = 200
    data: Optional[Any] = None
    raw_content: Optional[str] = None
    tier_used: str = "scraping_browser"
    error: Optional[str] = None
    response_time_ms: float = 0.0


class BrightDataClient:
    """Client for Bright Data Browser API via Playwright CDP WebSocket."""

    def __init__(self, config: Optional[BrightDataConfig] = None):
        self.config = config or BrightDataConfig()

    def validate_config(self) -> None:
        """Validates configuration, raising BrightDataConfigError if credentials are missing."""
        if not self.config.is_configured():
            raise BrightDataConfigError(
                "Bright Data credentials are missing. Set the BRIGHT_DATA_API_KEY environment variable."
            )

    def scrape(
        self,
        url: str,
        tier: str = "scraping_browser",
        schema: Optional[Dict[str, Any]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
    ) -> BrightDataResponse:
        """Executes real browser navigation and extraction via Bright Data Browser API."""
        start_time = time.perf_counter()

        if not self.config.is_configured():
            return BrightDataResponse(
                success=False,
                status_code=500,
                tier_used=tier,
                error="CONFIG_ERROR: BRIGHT_DATA_API_KEY is not configured",
                response_time_ms=0.0,
            )

        if sync_playwright is None:  # pragma: no cover
            return BrightDataResponse(
                success=False,
                status_code=500,
                tier_used=tier,
                error="CONFIG_ERROR: Playwright package is not installed",
                response_time_ms=0.0,
            )

        ws_endpoint = self.config.get_ws_endpoint()
        timeout_ms = int(self.config.timeout_seconds * 1000)

        try:
            with sync_playwright() as p:
                logger.info(
                    "Connecting to Bright Data Browser API at %s:%d (zone=%s)",
                    self.config.browser_host,
                    self.config.browser_port,
                    self.config.zone,
                )
                browser = p.chromium.connect_over_cdp(ws_endpoint, timeout=timeout_ms)
                try:
                    context = browser.new_context(
                        extra_http_headers=custom_headers or {}
                    )
                    page = context.new_page()
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

                    # Extract page content
                    html_content = page.content()
                    page_title = page.title()
                    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

                    # Try parsing as JSON if target is a JSON API or content is JSON text
                    extracted_data: Any = None
                    try:
                        # Strip HTML tags if plain JSON returned wrapped in pre/body
                        body_text = page.inner_text("body").strip()
                        if body_text.startswith("{") and body_text.endswith("}"):
                            extracted_data = json.loads(body_text)
                    except Exception:
                        pass

                    if extracted_data is None:
                        # Return extracted document representation
                        extracted_data = {
                            "title": page_title,
                            "content": html_content,
                        }

                    return BrightDataResponse(
                        success=True,
                        status_code=200,
                        data=extracted_data,
                        raw_content=html_content,
                        tier_used=tier,
                        response_time_ms=elapsed_ms,
                    )
                finally:
                    browser.close()

        except PlaywrightTimeoutError as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            safe_msg = _sanitize_error_message(str(e), self.config.api_key)
            logger.warning("Bright Data Browser navigation timeout: %s", safe_msg)
            return BrightDataResponse(
                success=False,
                status_code=504,
                tier_used=tier,
                error=f"TIMEOUT_ERROR: Browser navigation timed out ({safe_msg})",
                response_time_ms=elapsed_ms,
            )

        except PlaywrightError as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            raw_err = str(e)
            safe_msg = _sanitize_error_message(raw_err, self.config.api_key)

            # Classify specific error patterns
            if (
                "407" in raw_err
                or "403" in raw_err
                or "Forbidden" in raw_err
                or "Unauthorized" in raw_err
                or "401" in raw_err
                or "Auth Failed" in raw_err
            ):
                error_code = "HTTP_403_FORBIDDEN"
                status_code = 403
            elif "429" in raw_err or "Too Many Requests" in raw_err:
                error_code = "HTTP_429_TOO_MANY_REQUESTS"
                status_code = 429
            elif "Connection refused" in raw_err or "Target closed" in raw_err or "WebSocket" in raw_err or "net::ERR" in raw_err:
                error_code = "NETWORK_ERROR"
                status_code = 502
            else:
                error_code = "BROWSER_API_ERROR"
                status_code = 500

            logger.warning("Bright Data Browser API error [%s]: %s", error_code, safe_msg)
            return BrightDataResponse(
                success=False,
                status_code=status_code,
                raw_content=safe_msg,
                tier_used=tier,
                error=f"{error_code}: {safe_msg}",
                response_time_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            safe_msg = _sanitize_error_message(str(e), self.config.api_key)
            logger.exception("Unexpected error in Bright Data client: %s", safe_msg)
            return BrightDataResponse(
                success=False,
                status_code=500,
                tier_used=tier,
                error=f"UNEXPECTED_ERROR: {safe_msg}",
                response_time_ms=elapsed_ms,
            )
