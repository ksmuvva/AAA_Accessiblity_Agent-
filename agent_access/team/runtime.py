"""Run settings shared by every agent run: tracing, and a filter that runs before every model call.

The filter (trim_input) is the one place every model call of every agent passes through, nested specialist and
reviewer runs included. It charges the call to the run's budget (G1, agent_access/budget.py), keeps the input
affordable (old screenshots and tool outputs trimmed), and appends the agent's memory block (G4,
agent_access/memory.py: what the team knows about this site and lessons from past audits) to its instructions.
"""

from __future__ import annotations

from agents import ModelSettings, RunConfig
from agents.models.default_models import get_default_model, gpt_5_reasoning_settings_required
from agents.run_config import CallModelData, ModelInputData

from agent_access.budget import charge_model_call
from agent_access.context import AuditContext
from agent_access.memory import memory_block

# Screenshots are the largest items in an agent's input. Only the newest ones stay as images.
KEEP_IMAGES = 2
# Tool outputs older than the newest KEEP_FULL_OUTPUTS are cut to OLD_OUTPUT_CHARS.
KEEP_FULL_OUTPUTS = 12
OLD_OUTPUT_CHARS = 1_500
IMAGE_PLACEHOLDER = {"type": "input_text", "text": "[Earlier screenshot removed to save context. Take a new one if needed.]"}


def with_memory(data: CallModelData) -> str | None:
    """The agent's instructions plus its memory block. Memory never breaks a run: any error leaves them as they are."""
    instructions, audit = data.model_data.instructions, data.context
    if not isinstance(audit, AuditContext) or not isinstance(instructions, str) or data.agent is None:
        return instructions
    try:
        has_tools = any(getattr(t, "name", None) == "recall" for t in getattr(data.agent, "tools", None) or [])
        block = memory_block(audit, data.agent.name, has_tools)
    except Exception:  # e.g. a locked or corrupt memory file
        return instructions
    return f"{instructions}\n\n{block}" if block else instructions


def trim_input(data: CallModelData) -> ModelInputData:
    """Charge the call to the budget, drop all but the newest screenshots, shorten old tool outputs, and add memory.

    The full history stays in the run; only what is sent to the model is trimmed. Function-call items and
    their call ids are kept, so the conversation stays valid. budget.BudgetExhausted propagates (the SDK re-raises
    filter errors unchanged), so a run over budget stops at its next model call; budget.py decides who is exempt.
    """
    charge_model_call(data.context, getattr(data.agent, "name", None))
    items = list(data.model_data.input)
    outputs = [i for i, item in enumerate(items) if isinstance(item, dict) and item.get("type") == "function_call_output"]
    images = 0
    for rank, i in enumerate(reversed(outputs)):  # newest first
        output = items[i].get("output")
        if isinstance(output, list):
            trimmed = []
            for part in reversed(output):
                if isinstance(part, dict) and part.get("type") == "input_image":
                    images += 1
                    part = part if images <= KEEP_IMAGES else IMAGE_PLACEHOLDER
                trimmed.append(part)
            trimmed.reverse()
        elif isinstance(output, str) and rank >= KEEP_FULL_OUTPUTS and len(output) > OLD_OUTPUT_CHARS:
            trimmed = output[:OLD_OUTPUT_CHARS] + " ...[older tool output shortened]"
        else:
            continue
        if trimmed != output:
            items[i] = {**items[i], "output": trimmed}
    return ModelInputData(input=items, instructions=with_memory(data))


def settings(model, **kwargs) -> ModelSettings:
    """Every agent's model settings: nothing it sends (page text, screenshots) is stored at OpenAI. Without stored
    responses, a reasoning model carries its reasoning between calls encrypted instead of by a stored id."""
    name = (model if isinstance(model, str) else getattr(model, "model", None)) or get_default_model()
    reasoning = gpt_5_reasoning_settings_required(name) or name.startswith(("o1", "o3", "o4"))
    return ModelSettings(store=False, response_include=["reasoning.encrypted_content"] if reasoning else None, **kwargs)


def run_config(audit: AuditContext, workflow_name: str) -> RunConfig:
    return RunConfig(workflow_name=workflow_name, tracing_disabled=not audit.tracing, call_model_input_filter=trim_input)
