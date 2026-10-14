"""Agent definitions: lead auditor, specialists, explorer, verifier, reviewer, summary writer, and the orchestrator.

`build_team` wires them into one team. In handoff mode (agent mode's default) the lead hands the conversation to a
specialist, the explorer or the verifier through SDK handoffs, and each comes back through `return_to_lead`. In
tools mode the specialists are tools the lead calls (today's code-mode path, also used for parallel runs on forked
contexts); the explorer and verifier stay handoffs. `build_audit_agent(model)` alone is the unchanged code-mode lead.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents import Agent, RunResult
from agents.models.interface import Model

from agent_access.context import AuditContext, Delegation
from agent_access.memory import MEMORY_TOOLS
from agent_access.models import LeadNotes, SpecialistReport
from agent_access.rules import TOOLS_IN_ORDER
from agent_access.team.audit_agent import DELEGATIONS, build_audit_agent, delegation_mode
from agent_access.team.explorer import build_explorer, explore_tool
from agent_access.team.guardrails import apply_guardrails
from agent_access.team.handoffs import (
    HANDBACK,
    ExploreBrief,
    ExplorationReport,
    HandoffBrief,
    Verification,
    VerifyBrief,
    handback,
    to_subagent,
)
from agent_access.team.orchestrator import AuditOutcome, run_audit
from agent_access.team.reviewer import build_reviewer
from agent_access.team.specialists import SPECIALISTS, build_specialist, unjudged_criteria
from agent_access.team.summarizer import build_summarizer
from agent_access.team.verifier import build_verifier, verify_tool
from agent_access.agent_tools.planning import SPECIALIST_FOR_DOMAIN

@dataclass
class Team:
    lead: Agent[AuditContext]
    specialists: dict[str, Agent[AuditContext]]
    explorer: Agent[AuditContext]
    verifier: Agent[AuditContext]
    reviewer: Agent[AuditContext]
    summarizer: Agent[AuditContext]
    delegation: Delegation
    by_name: dict[str, Agent[AuditContext]]  # every agent above by Agent.name (parallel runs pick by name)


def _prepare(agent: Agent[AuditContext], *, memory: bool = True) -> Agent[AuditContext]:
    """Make an agent a team member: G4's memory tools (auditing agents only; code mode's agents keep their pinned
    tool counts), then G5's guardrails again (idempotent; the builders already applied them, but the tool guardrails
    attach per tool, so the new tools need them). Every specialist the team runs, by handoff, delegate tool or
    parallel fork, goes through here."""
    if memory:
        agent.tools = [*agent.tools, *MEMORY_TOOLS]
    return apply_guardrails(agent)


def _team_specialist(domain: str, model: str | Model | None) -> Agent[AuditContext]:
    """The builder the lead's delegate and parallel tools use in a team: a prepared tool-mode specialist."""
    return _prepare(build_specialist(domain, model))


def _specialist_description(domain: str) -> str:
    criteria = ", ".join(t.criterion for t in TOOLS_IN_ORDER if t.domain == domain)
    return (f"Hand the page to the {domain} specialist for {SPECIALISTS[domain][3]} ({criteria}). It judges and records "
            "its criteria on the loaded page, then returns to you with its report. Pass `focus` to point it at something "
            "specific (e.g. 'the checkout form'), or '' for everything in its area.")


def build_team(model: str | Model | None = None, audit: AuditContext | None = None, *,
               reviewer_model: str | Model | None = None, delegation: Delegation = "handoffs") -> Team:
    """Build the whole team around one lead. The lead gets a handoff per subagent; every subagent gets `return_to_lead`.
    The handoff tool names are the planning ids, so `get_open_items` means the same in both delegation modes."""
    delegation = delegation_mode(delegation)
    level = audit.target_level if audit is not None else "AA"
    lead = build_audit_agent(model, delegation=delegation, subagents=True, specialist_builder=_team_specialist)
    explorer = build_explorer(model)
    verifier = build_verifier(model)
    specialists = {d: build_specialist(d, model, hands_back=delegation == "handoffs") for d in SPECIALISTS}
    for agent in (explorer, verifier, *specialists.values()):
        _prepare(agent)
    if delegation == "handoffs":
        explorer.handoffs = [handback(lead, agent_name=explorer.name, report_type=ExplorationReport)]
        verifier.handoffs = [handback(lead, agent_name=verifier.name, report_type=Verification)]
        lead.handoffs = [
            to_subagent(explorer, tool_name="explore_page", input_type=ExploreBrief, description=(
                "Hand the page to the explorer: it clicks through menus, dialogs, search, sign-in (up to the form) and "
                "pagination, labels each state so the automated checks run on it, and returns with a map of states, "
                "flows and links. It hands back from the last state it labelled; judge that state, then return_to_page.")),
            to_subagent(verifier, tool_name="verify_judgement", input_type=VerifyBrief, description=(
                "Hand a fail or pass you are about to record to the verifier: it checks it on the page, records it with "
                "its own evidence (or needs_review when it can't confirm it), and returns with its verdict.")),
        ]
        for domain, specialist in specialists.items():
            specialist.handoffs = [handback(
                lead, agent_name=specialist.name, report_type=SpecialistReport,
                extra=lambda audit_, _report, d=domain: {"not_judged": unjudged_criteria(audit_, d, audit_.current_url)})]
            lead.handoffs.append(to_subagent(specialist, tool_name=SPECIALIST_FOR_DOMAIN[domain], input_type=HandoffBrief,
                                             description=_specialist_description(domain)))
    else:
        # Tools mode: no conversation transfers at all. The specialists are delegate tools (built per call by
        # _team_specialist); the explorer and verifier run as nested tools on the prepared agents above.
        lead.tools = [*lead.tools, explore_tool(explorer), verify_tool(verifier)]
    reviewer = build_reviewer(reviewer_model or model)
    summarizer = build_summarizer(model, level)
    _prepare(lead)  # last: after its handoffs or subagent tools exist
    _prepare(reviewer, memory=False)
    agents = [lead, *specialists.values(), explorer, verifier, reviewer, summarizer]
    return Team(lead=lead, specialists=specialists, explorer=explorer, verifier=verifier, reviewer=reviewer,
                summarizer=summarizer, delegation=delegation, by_name={a.name: a for a in agents})


def lead_must_resume(result: RunResult, team: Team) -> bool:
    """True when a run ended in a subagent (it answered instead of handing back): resume the lead with
    `Runner.run(team.lead, result.to_input_list(), ...)` so the page's final output is the lead's."""
    return result.last_agent is not team.lead


def final_notes(result: RunResult) -> list[str]:
    """The notes a run leaves for the human, whichever agent ended it."""
    output, name = result.final_output, result.last_agent.name
    if isinstance(output, LeadNotes):
        return list(output.notes_for_human)
    if isinstance(output, SpecialistReport):
        return [f"{name}: {u}" for u in output.unresolved]
    if isinstance(output, ExplorationReport):
        return [f"{name}: {b}" for b in output.blocked]
    if isinstance(output, Verification):
        return [f"{name}: {output.criterion} {output.verdict}: {output.reason}"]
    return []


__all__ = ["AuditOutcome", "DELEGATIONS", "HANDBACK", "Team", "build_audit_agent", "build_summarizer", "build_team",
           "delegation_mode", "final_notes", "lead_must_resume", "run_audit"]
