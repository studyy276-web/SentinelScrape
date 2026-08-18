"""Interfaces and mock implementations for orchestrator dependency injection."""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from app.orchestrator.context import OrchestrationContext


@runtime_checkable
class Collector(Protocol):
    """Protocol for data collection components."""

    def collect(self, context: OrchestrationContext) -> OrchestrationContext:
        """Executes data extraction for the given context."""
        ...


@runtime_checkable
class Validator(Protocol):
    """Protocol for data validation and trust score computation."""

    def validate(self, context: OrchestrationContext) -> OrchestrationContext:
        """Validates extracted data against schema/rules and computes trust score."""
        ...


@runtime_checkable
class Diagnoser(Protocol):
    """Protocol for analyzing failure signatures and determining healability."""

    def diagnose(self, context: OrchestrationContext) -> OrchestrationContext:
        """Diagnoses failure causes and assigns failure signatures."""
        ...


@runtime_checkable
class Healer(Protocol):
    """Protocol for applying selector/schema/pipeline self-healing."""

    def heal(self, context: OrchestrationContext) -> OrchestrationContext:
        """Applies healing strategies and updates context."""
        ...


@runtime_checkable
class Escalator(Protocol):
    """Protocol for escalating compute tier or scraping strategies."""

    def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
        """Escalates to a higher compute/browser tier."""
        ...


@runtime_checkable
class AIService(Protocol):
    """Protocol for post-verification AI processing."""

    def generate_answer(self, context: OrchestrationContext, prompt: Optional[str] = None) -> OrchestrationContext:
        """Processes verified data through an LLM service."""
        ...


# --- Default Deterministic Stubs for Testing & Mocking ---

class StubCollector:
    """Deterministic stub collector for testing."""

    def __init__(self, data_to_return: Optional[Any] = None, should_fail: bool = False):
        self.data_to_return = data_to_return or {"title": "Sample Title", "price": "$19.99"}
        self.should_fail = should_fail
        self.calls: int = 0

    def collect(self, context: OrchestrationContext) -> OrchestrationContext:
        self.calls += 1
        if self.should_fail:
            context.extracted_data = None
        else:
            context.extracted_data = self.data_to_return
        return context


class StubValidator:
    """Deterministic stub validator for testing."""

    def __init__(
        self,
        default_passed: bool = True,
        trust_score: float = 0.95,
        failed_fields: Optional[List[str]] = None,
    ):
        self.default_passed = default_passed
        self.trust_score = trust_score
        self.failed_fields = failed_fields or []
        self.calls: int = 0

    def validate(self, context: OrchestrationContext) -> OrchestrationContext:
        self.calls += 1
        passed = self.default_passed and (context.extracted_data is not None)
        score = self.trust_score if passed else 0.4
        fields = [] if passed else (self.failed_fields or ["missing_data"])

        context.trust_score = score
        context.failed_fields = fields
        context.verification_result = {
            "passed": passed,
            "score": score,
            "failed_fields": fields,
        }
        return context


class StubDiagnoser:
    """Deterministic stub diagnoser for testing."""

    def __init__(
        self,
        failure_signature: str = "SELECTOR_MISSING_PRICE",
        is_healable: bool = True,
    ):
        self.failure_signature = failure_signature
        self.is_healable = is_healable
        self.calls: int = 0

    def diagnose(self, context: OrchestrationContext) -> OrchestrationContext:
        self.calls += 1
        # Set or preserve failure signature
        if not context.failure_signature:
            context.failure_signature = self.failure_signature
        context.is_healable = self.is_healable
        return context


class StubHealer:
    """Deterministic stub healer for testing."""

    def __init__(
        self,
        success_on_attempt: int = 1,
        healed_data: Optional[Any] = None,
        healing_source: str = "CSS_SELECTOR_FALLBACK",
    ):
        self.success_on_attempt = success_on_attempt
        self.healed_data = healed_data or {"title": "Healed Title", "price": "$29.99"}
        self.healing_source = healing_source
        self.calls: int = 0

    def heal(self, context: OrchestrationContext) -> OrchestrationContext:
        self.calls += 1
        context.healing_source = self.healing_source
        # If this attempt number meets or exceeds success threshold, heal data
        if context.healing_attempts >= self.success_on_attempt:
            context.metadata["healed"] = True
            context.extracted_data = self.healed_data
        else:
            context.metadata["healed"] = False
        return context


class StubEscalator:
    """Deterministic stub escalator for testing."""

    def __init__(self, succeed: bool = True, target_tier: str = "unblocker_browser"):
        self.succeed = succeed
        self.target_tier = target_tier
        self.calls: int = 0

    def escalate(self, context: OrchestrationContext) -> OrchestrationContext:
        self.calls += 1
        if self.succeed:
            context.compute_tier = self.target_tier
            context.metadata["escalation_succeeded"] = True
        else:
            context.metadata["escalation_succeeded"] = False
        return context


class StubAIService:
    """Deterministic stub AI service for testing."""

    def __init__(self, default_answer: str = "Processed AI insight based on verified data."):
        self.default_answer = default_answer
        self.calls: int = 0
        self.received_data: List[Any] = []

    def generate_answer(self, context: OrchestrationContext, prompt: Optional[str] = None) -> OrchestrationContext:
        self.calls += 1
        # Invariant check: never process unverified data
        if not context.is_verified():
            raise RuntimeError("AIService received unverified data! Security and Trust violation.")

        self.received_data.append(context.extracted_data)
        context.ai_answer = f"{self.default_answer} Prompt: {prompt or context.ai_prompt or 'default'}"
        return context
