"""
Tool Broker — the authoritative execution boundary for agent actions.

No model-generated action executes directly. Every tool invocation flows:

    model -> tool proposal -> ToolBroker
        -> identity/context
        -> permission evaluation
        -> contextual risk evaluation
        -> policy decision (ALLOW | DENY | REQUIRE_APPROVAL)
        -> human approval when required (payload-hash bound)
        -> validated execution
        -> sanitized result
        -> hierarchical DecisionTrace events
"""

from helios.broker.core import ToolBroker, BrokerResult  # noqa: F401
from helios.broker.manifest import ToolManifest  # noqa: F401
from helios.broker.registry import ToolRegistry, default_registry  # noqa: F401
from helios.broker.types import (  # noqa: F401
    InvocationContext,
    RiskAssessment,
    PolicyDecision,
    PermissionDecision,
    RISK_LEVELS,
)
