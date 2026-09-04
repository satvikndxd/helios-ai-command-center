"""
Scripted provider — deterministic agent behavior with zero API keys.

If the conversation contains `SCRIPT:[...]` (a JSON list of planner steps),
each model call returns the next step. This powers the local demo and the
end-to-end tests: the full governed loop — proposals, permissions, risk,
policy, approvals, execution, trace — runs for real; only the "model" is
scripted.
"""

from __future__ import annotations

import asyncio
import json
import re

from helios.config import Settings
from helios.providers.base import BaseProvider, ProviderResult

_SCRIPT_RE = re.compile(r"SCRIPT:(\[.*?\])(?:\s*$|\n)", re.DOTALL)


class ScriptedProvider(BaseProvider):
    async def complete(self, request: dict, settings: Settings) -> ProviderResult:
        await asyncio.sleep(0)
        input_text = request.get("input_text", "")
        step = int(request.get("agent_step", 0))

        output: dict = {"type": "final", "content": "No SCRIPT found in conversation."}
        matches = list(_SCRIPT_RE.finditer(input_text))
        match = matches[-1] if matches else None  # newest script wins
        if match:
            try:
                steps = json.loads(match.group(1))
                if step < len(steps):
                    output = steps[step]
                else:
                    output = {"type": "final", "content": "Script complete."}
            except json.JSONDecodeError:
                output = {"type": "final", "content": "Script was not valid JSON."}

        text = json.dumps(output)
        usage = {"prompt_tokens": max(1, len(input_text) // 4),
                 "completion_tokens": max(1, len(text) // 4)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return ProviderResult(
            output_text=text, provider="scripted",
            model=request.get("model") or "scripted-1",
            usage=usage, raw={"step": step}, citations=[],
        )
