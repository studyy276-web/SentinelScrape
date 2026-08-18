"""Orchestrator state definitions."""

from enum import Enum


class OrchestratorState(str, Enum):
    """Explicit lifecycle states for the SentinelScrape orchestration machine."""

    START = "START"
    COLLECTING = "COLLECTING"
    VALIDATING = "VALIDATING"
    TRUST_GATE = "TRUST_GATE"
    DIAGNOSING = "DIAGNOSING"
    HEALING = "HEALING"
    ESCALATING = "ESCALATING"
    VERIFIED = "VERIFIED"
    AI_READY = "AI_READY"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
