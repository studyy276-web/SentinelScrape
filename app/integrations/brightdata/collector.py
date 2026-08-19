"""Bright Data Collector adapter implementing orchestrator Collector protocol."""

import logging
from typing import Optional

from app.integrations.brightdata.client import BrightDataClient
from app.orchestrator.context import OrchestrationContext

logger = logging.getLogger(__name__)


class BrightDataCollector:
    """Collector component integrating Bright Data API with the SentinelScrape pipeline."""

    def __init__(self, client: Optional[BrightDataClient] = None):
        self.client = client or BrightDataClient()

    def collect(self, context: OrchestrationContext) -> OrchestrationContext:
        """Executes data extraction via Bright Data Scraper Studio."""
        logger.info(
            "Collecting url=%s tier=%s via Bright Data",
            context.url,
            context.compute_tier or "standard",
        )

        response = self.client.scrape(
            url=context.url,
            tier=context.compute_tier or "standard",
            schema=context.schema,
        )

        if response.success:
            context.extracted_data = response.data
            context.metadata["brightdata"] = {
                "status_code": response.status_code,
                "tier_used": response.tier_used,
                "response_time_ms": response.response_time_ms,
            }
        else:
            context.extracted_data = None
            context.metadata["brightdata_error"] = response.error
            if response.error:
                clean_sig = response.error.split(":")[0].strip()
                if not context.failure_signature:
                    context.failure_signature = clean_sig

        return context
