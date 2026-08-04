from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from helios.models import DecisionTrace


@dataclass
class EvalResult:
    """
    Normalized result from a single evaluator.

    score: 0.0 (failed) .. 1.0 (perfect)
    passed: hard pass/fail for gating and dashboards
    details: evaluator-specific evidence (kept for auditability)
    """

    evaluator: str
    score: float
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


class BaseEvaluator(ABC):
    """
    Strategy interface for evaluators.

    Phase 1.5 ships deterministic heuristics. LLM-as-judge evaluators (Phase 2)
    implement this same interface, so the pipeline and worker never change.
    """

    name: str

    @abstractmethod
    def evaluate(self, trace: DecisionTrace) -> EvalResult:
        raise NotImplementedError
