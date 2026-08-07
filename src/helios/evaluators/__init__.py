from helios.evaluators.base import BaseEvaluator, EvalResult
from helios.evaluators.heuristics import (
    EmptyOutputEvaluator,
    LatencyEvaluator,
    RefusalEvaluator,
)
from helios.evaluators.pipeline import EvaluationPipeline


def default_pipeline() -> EvaluationPipeline:
    """The Phase 1.5 heuristic pipeline used by the worker."""
    return EvaluationPipeline(
        [
            EmptyOutputEvaluator(),
            LatencyEvaluator(max_ms=5000),
            RefusalEvaluator(),
        ]
    )


__all__ = [
    "BaseEvaluator",
    "EvalResult",
    "EmptyOutputEvaluator",
    "LatencyEvaluator",
    "RefusalEvaluator",
    "EvaluationPipeline",
    "default_pipeline",
]
