"""Deterministic compute tier escalation engine implementing orchestrator Escalator protocol."""

import logging
from typing import Dict, List, Optional
from app.orchestrator.context import OrchestrationContext

logger = logging.getLogger(__name__)

# Standard ordered hierarchy for compute/proxy tiers
DEFAULT_TIER_ORDER: List[str] = [
    "standard",
    "residential_proxy",
    "scraping_browser",
    "unblocker_browser",
    "premium",
]


class SentinelEscalator:
    """Deterministic escalation engine for upgrading compute tiers when anti-bot or blocking occurs."""

    def __init__(self, tier_order: Optional[List[str]] = None):
        self.tier_order = tier_order or list(DEFAULT_TIER_ORDER)

    def determine_next_tier(self, current_tier: Optional[str], failure_signature: Optional[str] = None) -> str:
        """Calculates the target tier based on current tier and failure characteristics."""
        curr = (current_tier or "standard").lower()
        sig = (failure_signature or "").upper()

        # If severe anti-bot/infrastructure block (403, Cloudflare, CAPTCHA), jump to unblocker/premium
        if any(k in sig for k in ("403", "429", "CLOUDFLARE", "CAPTCHA", "BOT_DETECT", "BLOCKED")):
            if curr in ("unblocker_browser", "premium"):
                return "premium"
            return "unblocker_browser"

        # Standard sequential tier progression
        try:
            curr_idx = self.tier_order.index(curr)
            next_idx = min(curr_idx + 1, len(self.tier_order) - 1)
            return self.tier_order[next_idx]
        except ValueError:
            # If current tier not in standard hierarchy, default to scraping_browser
            return "scraping_browser"

    def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
        """Applies deterministic tier escalation to the context."""
        prev_tier = context.compute_tier or "standard"
        next_tier = self.determine_next_tier(
            current_tier=prev_tier,
            failure_signature=context.failure_signature,
        )

        logger.info(
            "Escalating tier from %s to %s for url=%s (reason=%s)",
            prev_tier,
            next_tier,
            context.url,
            context.failure_signature,
        )

        context.compute_tier = next_tier
        context.metadata["escalation_succeeded"] = True
        context.metadata["escalation_details"] = {
            "from_tier": prev_tier,
            "to_tier": next_tier,
            "attempt": context.escalation_attempts,
            "reason": context.failure_signature,
        }

        return context
