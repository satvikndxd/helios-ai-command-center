from helios.evaluators.base import BaseEvaluator, EvalResult
from helios.evaluators.groundedness import GroundednessEvaluator
from helios.evaluators.heuristics import (
    EmptyOutputEvaluator,
    LatencyEvaluator,
    RefusalEvaluator,
)
from helios.evaluators.pipeline import EvaluationPipeline


def default_pipeline() -> EvaluationPipeline:
    """The default heuristic pipeline used by the worker and simulator."""
    return EvaluationPipeline(
        [
            EmptyOutputEvaluator(),
            LatencyEvaluator(max_ms=5000),
            RefusalEvaluator(),
            GroundednessEvaluator(),
        ]
    )


__all__ = [
    "BaseEvaluator",
    "EvalResult",
    "EmptyOutputEvaluator",
    "LatencyEvaluator",
    "RefusalEvaluator",
    "GroundednessEvaluator",
    "EvaluationPipeline",
    "default_pipeline",
]
