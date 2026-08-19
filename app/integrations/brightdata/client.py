"""Bright Data Scraper Studio API client and configuration."""

import logging
import os
import time
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field

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
    """Configuration settings for Bright Data integration."""

    model_config = ConfigDict(populate_by_name=True)

    api_key: Optional[str] = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_API_KEY"))
    zone: str = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_ZONE", "web_unlocker"))
    base_url: str = Field(default_factory=lambda: os.getenv("BRIGHT_DATA_BASE_URL", "https://api.brightdata.com"))
    timeout_seconds: float = 30.0

    def is_configured(self) -> bool:
        """Returns True if a valid API key is present."""
        return bool(self.api_key and self.api_key.strip())

    def __repr__(self) -> str:
        """Masks sensitive API key in string representation."""
        masked_key = "***" if self.api_key else "None"
        return f"BrightDataConfig(zone='{self.zone}', base_url='{self.base_url}', api_key='{masked_key}')"

    def __str__(self) -> str:
        return self.__repr__()


class BrightDataResponse(BaseModel):
    """Normalized response from Bright Data scraping execution."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    status_code: int = 200
    data: Optional[Any] = None
    raw_content: Optional[str] = None
    tier_used: str = "standard"
    error: Optional[str] = None
    response_time_ms: float = 0.0


class BrightDataClient:
    """Isolated client for Bright Data Scraper Studio and Web Unlocker API."""

    def __init__(self, config: Optional[BrightDataConfig] = None):
        self.config = config or BrightDataConfig()

    def validate_config(self) -> None:
        """Validates configuration, raising BrightDataConfigError if credentials are missing."""
        if not self.config.is_configured():
            raise BrightDataConfigError(
                "Bright Data API key is missing. Set the BRIGHT_DATA_API_KEY environment variable."
            )

    def scrape(
        self,
        url: str,
        tier: str = "standard",
        schema: Optional[Dict[str, Any]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
    ) -> BrightDataResponse:
        """Executes a scraping request against Bright Data endpoint.
        
        Guarantees safe failure handling without leaking API credentials.
        """
        start_time = time.perf_counter()

        if not self.config.is_configured():
            return BrightDataResponse(
                success=False,
                status_code=500,
                tier_used=tier,
                error="CONFIG_ERROR: BRIGHT_DATA_API_KEY is not configured",
                response_time_ms=0.0,
            )

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            **(custom_headers or {}),
        }

        payload = {
            "url": url,
            "zone": self.config.zone,
            "tier": tier,
            "format": "json" if schema else "raw",
        }
        if schema:
            payload["schema"] = schema

        endpoint = f"{self.config.base_url.rstrip('/')}/request"

        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(endpoint, json=payload, headers=headers)
                elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

                if response.status_code in (200, 201):
                    try:
                        data = response.json()
                        extracted = data.get("response", data.get("data", data))
                    except Exception:
                        extracted = {"content": response.text}

                    return BrightDataResponse(
                        success=True,
                        status_code=response.status_code,
                        data=extracted,
                        raw_content=response.text,
                        tier_used=tier,
                        response_time_ms=elapsed_ms,
                    )
                else:
                    error_msg = f"HTTP_{response.status_code}"
                    if response.status_code == 403:
                        error_msg = "HTTP_403_FORBIDDEN"
                    elif response.status_code == 429:
                        error_msg = "HTTP_429_TOO_MANY_REQUESTS"

                    return BrightDataResponse(
                        success=False,
                        status_code=response.status_code,
                        tier_used=tier,
                        error=error_msg,
                        response_time_ms=elapsed_ms,
                    )

        except httpx.TimeoutException:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.warning("Bright Data request to %s timed out after %.2f ms", url, elapsed_ms)
            return BrightDataResponse(
                success=False,
                status_code=504,
                tier_used=tier,
                error="TIMEOUT_ERROR: Request timed out",
                response_time_ms=elapsed_ms,
            )

        except httpx.RequestError as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.warning("Bright Data network error: %s", str(e))
            return BrightDataResponse(
                success=False,
                status_code=502,
                tier_used=tier,
                error="NETWORK_ERROR: Connection failed",
                response_time_ms=elapsed_ms,
            )

        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception("Unexpected error during Bright Data scrape: %s", str(e))
            return BrightDataResponse(
                success=False,
                status_code=500,
                tier_used=tier,
                error="UNEXPECTED_ERROR",
                response_time_ms=elapsed_ms,
            )
