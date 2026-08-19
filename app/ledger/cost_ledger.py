"""Cost tracking ledger for recording compute tier usage, token consumption, and dollar costs."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TierPricing(BaseModel):
    """Pricing rates per compute tier in USD."""

    model_config = ConfigDict(populate_by_name=True)

    standard: float = 0.001            # Standard HTTP/DOM scrape
    residential_proxy: float = 0.010   # Residential rotating proxy
    scraping_browser: float = 0.015    # Headless scraping browser
    unblocker_browser: float = 0.025   # Full unblocker / stealth browser
    premium: float = 0.030             # Highest tier compute


# Token pricing per 1,000 tokens in USD
DEFAULT_INPUT_TOKEN_PRICE_PER_1K: float = 0.0001
DEFAULT_OUTPUT_TOKEN_PRICE_PER_1K: float = 0.0004
DEFAULT_HEALING_COST_USD: float = 0.002


class CostEntry(BaseModel):
    """Individual recorded cost event."""

    operation: str
    tier: Optional[str] = None
    cost_usd: float = 0.0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CostLedger:
    """Deterministic in-memory ledger tracking all financial and token costs for a request."""

    def __init__(
        self,
        pricing: Optional[TierPricing] = None,
        initial_data: Optional[Dict[str, Any]] = None,
    ):
        self.pricing = pricing or TierPricing()
        self.total_cost_usd: float = 0.0
        self.total_tokens: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.api_calls: int = 0
        self.tier_usage: Dict[str, int] = {}
        self.breakdown: Dict[str, float] = {
            "collection": 0.0,
            "healing": 0.0,
            "escalation": 0.0,
            "ai": 0.0,
            "other": 0.0,
        }
        self.events: List[Dict[str, Any]] = []

        if initial_data:
            self._hydrate(initial_data)

    def _hydrate(self, data: Dict[str, Any]) -> None:
        """Hydrates ledger from an existing dictionary."""
        self.total_cost_usd = float(data.get("total_cost_usd", data.get("usd", 0.0)))
        self.total_tokens = int(data.get("total_tokens", data.get("tokens", 0)))
        self.prompt_tokens = int(data.get("prompt_tokens", 0))
        self.completion_tokens = int(data.get("completion_tokens", 0))
        self.api_calls = int(data.get("api_calls", 0))
        self.tier_usage = dict(data.get("tier_usage", {}))
        if "breakdown" in data and isinstance(data["breakdown"], dict):
            for k, v in data["breakdown"].items():
                self.breakdown[k] = float(v)
        if "events" in data and isinstance(data["events"], list):
            self.events = list(data["events"])

    def get_tier_rate(self, tier: Optional[str]) -> float:
        """Returns the USD cost for a given compute tier."""
        if not tier:
            return self.pricing.standard
        tier_clean = tier.lower().strip()
        if hasattr(self.pricing, tier_clean):
            return getattr(self.pricing, tier_clean)
        return self.pricing.standard

    def record_collection(
        self,
        tier: str = "standard",
        cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Records a data collection scrape attempt."""
        rate = cost_usd if cost_usd is not None else self.get_tier_rate(tier)
        self.total_cost_usd = round(self.total_cost_usd + rate, 6)
        self.api_calls += 1
        self.tier_usage[tier] = self.tier_usage.get(tier, 0) + 1
        self.breakdown["collection"] = round(self.breakdown["collection"] + rate, 6)

        entry = CostEntry(
            operation="collection",
            tier=tier,
            cost_usd=rate,
            metadata=metadata or {},
        )
        self.events.append(entry.model_dump())
        return rate

    def record_healing(
        self,
        source: str = "CSS_SELECTOR_FALLBACK",
        cost_usd: Optional[float] = None,
        tokens: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Records a self-healing diagnosis/selector synthesis attempt."""
        rate = cost_usd if cost_usd is not None else DEFAULT_HEALING_COST_USD
        self.total_cost_usd = round(self.total_cost_usd + rate, 6)
        self.total_tokens += tokens
        self.breakdown["healing"] = round(self.breakdown["healing"] + rate, 6)

        entry = CostEntry(
            operation="healing",
            cost_usd=rate,
            tokens=tokens,
            metadata={"source": source, **(metadata or {})},
        )
        self.events.append(entry.model_dump())
        return rate

    def record_escalation(
        self,
        from_tier: str,
        to_tier: str,
        cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Records a compute tier escalation event."""
        diff = round(max(0.0, self.get_tier_rate(to_tier) - self.get_tier_rate(from_tier)), 6)
        rate = cost_usd if cost_usd is not None else diff
        self.total_cost_usd = round(self.total_cost_usd + rate, 6)
        self.breakdown["escalation"] = round(self.breakdown["escalation"] + rate, 6)

        entry = CostEntry(
            operation="escalation",
            tier=to_tier,
            cost_usd=rate,
            metadata={"from_tier": from_tier, "to_tier": to_tier, **(metadata or {})},
        )
        self.events.append(entry.model_dump())
        return rate

    def record_ai_generation(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Records post-verification AI LLM inference token usage and cost."""
        if cost_usd is not None:
            rate = cost_usd
        else:
            input_cost = (prompt_tokens / 1000.0) * DEFAULT_INPUT_TOKEN_PRICE_PER_1K
            output_cost = (completion_tokens / 1000.0) * DEFAULT_OUTPUT_TOKEN_PRICE_PER_1K
            rate = round(input_cost + output_cost, 6)

        tot_tok = prompt_tokens + completion_tokens
        self.total_cost_usd = round(self.total_cost_usd + rate, 6)
        self.total_tokens += tot_tok
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.api_calls += 1
        self.breakdown["ai"] = round(self.breakdown["ai"] + rate, 6)

        entry = CostEntry(
            operation="ai_generation",
            cost_usd=rate,
            tokens=tot_tok,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            metadata=metadata or {},
        )
        self.events.append(entry.model_dump())
        return rate

    def to_dict(self) -> Dict[str, Any]:
        """Exports full ledger summary as a serializable dictionary."""
        return {
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "api_calls": self.api_calls,
            "tier_usage": dict(self.tier_usage),
            "breakdown": dict(self.breakdown),
            "events": list(self.events),
        }
