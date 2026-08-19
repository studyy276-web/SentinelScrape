"""Budget Guard for enforcing spend limits and preventing runaway scraping or AI costs."""

import logging
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, ConfigDict

from app.ledger.cost_ledger import CostLedger
from app.orchestrator.context import OrchestrationContext
from app.orchestrator.states import OrchestratorState

logger = logging.getLogger(__name__)


class BudgetConfig(BaseModel):
    """Budget constraints and spending caps for orchestration executions."""

    model_config = ConfigDict(populate_by_name=True)

    max_budget_usd: Optional[float] = 0.10    # Maximum allowable spend per request in USD
    max_total_tokens: Optional[int] = None    # Maximum total LLM tokens allowed
    max_api_calls: Optional[int] = None       # Maximum API calls allowed


class BudgetGuard:
    """Deterministic guard enforcing financial and resource budgets across pipeline stages."""

    def __init__(self, config: Optional[BudgetConfig] = None):
        self.config = config or BudgetConfig()

    def can_afford(
        self,
        ledger: CostLedger,
        proposed_cost_usd: float = 0.0,
        proposed_tokens: int = 0,
    ) -> Tuple[bool, Optional[str]]:
        """Checks if a proposed expenditure fits within the configured budget limits.
        
        Returns:
            (True, None) if within budget.
            (False, "REASON_TAG") if exceeding budget limits.
        """
        # Check USD budget
        if self.config.max_budget_usd is not None:
            projected_usd = round(ledger.total_cost_usd + proposed_cost_usd, 6)
            if projected_usd > self.config.max_budget_usd:
                reason = (
                    f"BUDGET_EXCEEDED:USD_LIMIT "
                    f"(projected=${projected_usd:.4f} > limit=${self.config.max_budget_usd:.4f})"
                )
                return False, reason

        # Check Token budget
        if self.config.max_total_tokens is not None:
            projected_tokens = ledger.total_tokens + proposed_tokens
            if projected_tokens > self.config.max_total_tokens:
                reason = (
                    f"BUDGET_EXCEEDED:TOKEN_LIMIT "
                    f"(projected={projected_tokens} > limit={self.config.max_total_tokens})"
                )
                return False, reason

        # Check API Call count budget
        if self.config.max_api_calls is not None:
            projected_calls = ledger.api_calls + 1
            if projected_calls > self.config.max_api_calls:
                reason = (
                    f"BUDGET_EXCEEDED:API_CALL_LIMIT "
                    f"(projected={projected_calls} > limit={self.config.max_api_calls})"
                )
                return False, reason

        return True, None

    def enforce(
        self,
        context: OrchestrationContext,
        ledger: CostLedger,
        proposed_cost_usd: float = 0.0,
        proposed_tokens: int = 0,
    ) -> bool:
        """Enforces budget constraints on the given context.
        
        If budget is exceeded:
        - Sets context.status = OrchestratorState.BLOCKED
        - Assigns failure_signature
        - Sets metadata["budget_exceeded"] = True
        - Returns False
        
        If within budget:
        - Returns True
        """
        is_affordable, reason = self.can_afford(
            ledger=ledger,
            proposed_cost_usd=proposed_cost_usd,
            proposed_tokens=proposed_tokens,
        )

        if not is_affordable:
            logger.warning("Budget limit triggered: %s", reason)
            sig_tag = reason.split(" ")[0] if reason else "BUDGET_EXCEEDED"
            context.record_state(OrchestratorState.BLOCKED)
            context.failure_signature = sig_tag
            context.metadata["budget_exceeded"] = True
            context.metadata["budget_error"] = reason
            context.cost_ledger = ledger.to_dict()
            return False

        return True
