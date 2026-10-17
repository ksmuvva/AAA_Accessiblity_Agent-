"""SDK handoffs between the lead auditor and its subagents (specialists, explorer, verifier).

A handoff transfers the conversation: the subagent continues the same run with its own tools and instructions, and
comes back through a handback handoff (`return_to_lead`) whose input is its report, so the lead sees the report in
the transcript and the code keeps a log of every transfer in `audit.handoffs`.

The handoff tool names are the planning ids (`audit_forms`, `audit_media`, `audit_navigation`, `explore_page`,
`verify_judgement`), so `get_open_items`' specialist names mean the same thing whether specialists are handoffs or
tools (`build_team(delegation=...)`), and the coverage loop marks the same ids as tried.
"""

from __future__ import annotations

import json
from typing import Callable, Literal

from agents import Agent, Handoff, RunContextWrapper, Runner, handoff
from agents.exceptions import AgentsException
from agents.handoffs import HandoffInputData
from agents.items import MessageOutputItem
from pydantic import BaseModel, Field

from agent_access.context import AuditContext
from agent_access.team.runtime import run_config

LEAD_NAME = "Lead auditor"
# The handback handoff on every subagent. Stable: the planner (G1) checks last_agent against it.
HANDBACK = "return_to_lead"


# ---------------------------------------------------------------- what a handoff carries

class HandoffBrief(BaseModel):
    """The lead's brief when it hands the page to a specialist."""

    focus: str = Field(description="What to look at first (e.g. 'the checkout form'), or '' for everything in the area.")


class ExploreBrief(BaseModel):
    goal: str = Field(description="What to find or reach: e.g. 'open every menu', 'get to the sign-in error state'.")


class VerifyBrief(BaseModel):
    """A judgement the lead wants verified on the page before it is recorded."""

    criterion: str = Field(description='Criterion number such as "1.1.1".')
    status: Literal["fail", "pass"]
    description: str = Field(description="One-sentence statement of the judgement.")
    evidence: str = Field(description="What the lead saw that supports it.")
    selectors: list[str] = Field(description="CSS selectors of the relevant elements; an empty list if none.")


class ExploredState(BaseModel):
    label: str = Field(description="The label given to label_page_state.")
    how_reached: str = Field(description="The steps from the loaded page to this state.")
    what_changed: str = Field(description="What the state shows that the loaded page did not.")


class ExplorationReport(BaseModel):
    """The explorer's report to the lead."""

    states: list[ExploredState] = Field(description="Page states labelled during exploration, in order.")
    flows: list[str] = Field(description="Multi-step processes worth a full walk-through (sign-up, checkout, search).")
    links_worth_auditing: list[str] = Field(description="Same-site URLs that look like different templates or functions.")
    blocked: list[str] = Field(description="Steps that were blocked (server writes) or could not be done, and why.")


class Verification(BaseModel):
    """The verifier's report to the lead."""

    criterion: str
    verdict: Literal["confirmed", "rejected", "uncertain"]
    reason: str = Field(description="What you checked and what you saw.")


# ---------------------------------------------------------------- the log

def handoff_log(audit: AuditContext) -> list[dict]:
    """Every transfer in this run, in order (`audit.handoffs`, shared by the context's forks)."""
    return audit.handoffs


def _owner(audit: AuditContext) -> str | None:
    return audit.owner  # None on the root; forks name their owner


def latest_brief(audit: AuditContext, agent_name: str) -> dict | None:
    """The brief of the handoff `agent_name` is currently working on: the most recent transfer to it on this context
    with no later handback from it. Not keyed by page: label_page_state moves the page key mid-handoff."""
    for entry in reversed(handoff_log(audit)):
        if entry.get("owner") != _owner(audit):
            continue
        if entry.get("from") == agent_name and entry.get("tool") == HANDBACK:
            return None
        if entry.get("to") == agent_name and "input" in entry:
            return entry["input"]
    return None


def brief_section(audit: AuditContext, agent_name: str) -> str:
    """Appended to a subagent's instructions: the handoff call itself is filtered out of the history it receives
    (see trim_for_subagent), so the brief travels here instead."""
    brief = latest_brief(audit, agent_name)
    if not brief:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in brief.items())
    return f"\n## Brief from the lead auditor (page {audit.current_url})\n{lines}\n"


# ---------------------------------------------------------------- history the subagent receives

def _messages_only(items: tuple) -> tuple:
    return tuple(i for i in items if isinstance(i, MessageOutputItem))


def trim_for_subagent(data: HandoffInputData) -> HandoffInputData:
    """What a subagent sees of the lead's transcript: the lead's prompt and its messages, nothing else. The lead's
    tool calls, outputs, screenshots, handoff items and reasoning items are dropped, as the SDK's own
    remove_all_tools does: they are large, the subagent reads the page with its own tools, and a reasoning model
    rejects a function_call item sent back without its paired reasoning item. The brief reaches the subagent
    through its instructions (brief_section)."""
    history = data.input_history
    if isinstance(history, tuple):
        history = tuple(i for i in history if isinstance(i, dict) and (i.get("type") in (None, "message")))
    return data.clone(input_history=history, pre_handoff_items=_messages_only(data.pre_handoff_items),
                      new_items=_messages_only(data.new_items), input_items=None)


# ---------------------------------------------------------------- subagents as tools (tools mode)

def report_of(result, agent_name: str) -> dict:
    """A nested run's final output as a report for the lead, or the error that stopped it (findings are kept)."""
    if isinstance(result, Exception):
        return {"error": f"{agent_name} stopped early: {type(result).__name__}. Its recorded findings are kept."}
    return result.final_output.model_dump(mode="json")


async def run_subagent(audit: AuditContext, agent: Agent[AuditContext], tool_name: str, brief: BaseModel,
                       max_turns: int) -> dict:
    """The one nested-run path for subagents run as tools (delegate tools, and the explorer and verifier in tools
    mode, where there are no handoffs). Same bookkeeping as a handoff: the id is marked tried, the brief is logged
    (brief_section shows it to agents with dynamic instructions; the prompt carries it too) and the report is logged
    on return. No lock here: the subagent's own tools take it."""
    if not audit.has_page():
        return {"error": "No page is loaded yet."}
    audit.mark_attempted(tool_name)
    log = handoff_log(audit)
    log.append({"from": LEAD_NAME, "to": agent.name, "page": audit.current_url, "owner": _owner(audit),
                "tool": tool_name, "input": brief.model_dump(mode="json")})
    prompt = f"Page: {audit.current_url}\nBrief from the lead auditor: {brief.model_dump_json()}"
    try:
        result = await Runner.run(agent, prompt, context=audit, max_turns=max_turns,
                                  run_config=run_config(audit, f"WCAG audit: {tool_name}"))
    except AgentsException as exc:
        result = exc
    report = report_of(result, agent.name)
    log.append({"from": agent.name, "to": LEAD_NAME, "page": audit.current_url, "owner": _owner(audit),
                "tool": HANDBACK, "report": report})
    return report


# ---------------------------------------------------------------- building handoffs

def to_subagent(agent: Agent[AuditContext], *, tool_name: str, description: str, input_type: type[BaseModel]) -> Handoff:
    """Lead -> subagent. Marks `tool_name` as tried on the page (the coverage loop's id) and logs the brief."""

    def on_handoff(ctx: RunContextWrapper[AuditContext], brief: BaseModel) -> None:
        audit = ctx.context
        audit.mark_attempted(tool_name)
        handoff_log(audit).append({"from": LEAD_NAME, "to": agent.name, "page": audit.current_url, "owner": _owner(audit),
                                   "tool": tool_name, "input": brief.model_dump(mode="json")})

    return handoff(agent, tool_name_override=tool_name, tool_description_override=description, on_handoff=on_handoff,
                   input_type=input_type, input_filter=trim_for_subagent)


def handback(lead: Agent[AuditContext], *, agent_name: str, report_type: type[BaseModel],
             extra: Callable[[AuditContext, BaseModel], dict] | None = None) -> Handoff:
    """Subagent -> lead, carrying the subagent's report as the handoff input. `extra(audit, report)` adds fields to
    the log entry (a specialist adds the criteria it left unjudged). The lead keeps the full transcript: the
    run-level trim_input filter already bounds old tool outputs and screenshots."""

    def on_handoff(ctx: RunContextWrapper[AuditContext], report: BaseModel) -> None:
        audit = ctx.context
        entry = {"from": agent_name, "to": lead.name, "page": audit.current_url, "owner": _owner(audit),
                 "tool": HANDBACK, "report": report.model_dump(mode="json")}
        if extra is not None:
            entry.update(extra(audit, report))
        handoff_log(audit).append(entry)

    return handoff(lead, tool_name_override=HANDBACK, on_handoff=on_handoff, input_type=report_type,
                   tool_description_override=(
                       "Return to the lead auditor with your report. Call this when your work on the page is done; "
                       "the lead continues the audit. Your findings are already recorded."))
