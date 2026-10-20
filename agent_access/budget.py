"""The run's budget in agentic mode: model calls, wall-clock minutes, and pages.

The planner (team/planner.py) decides how to spend it. Two floors (see .claude/AGENTIC_PLAN.md, decisions 4 and 6):
`exhausted` only stops NEW agent work; `reserve_calls` stays available for the closing stages (reviewer, summary),
which code runs whatever the planner decided. Accounting: runtime.run_config's model-input filter calls
`charge_model_call(context, agent_name)` on every LLM request, nested runs included.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from agents.exceptions import AgentsException


class BudgetExhausted(AgentsException):
    """Raised by the model-input filter (runtime.trim_input) when even the reserve is spent (hard_exhausted). The soft
    limit (`exhausted`) never raises: the planner's tools refuse new work instead, so review and summary still run."""


@dataclass
class Budget:
    max_model_calls: int = 400
    max_minutes: float = 60.0
    max_pages: int = 10
    reserve_calls: int = 60          # kept for the reviewer and the summary writer
    review_calls_per_page: int = 20  # rough: reviewing one more page, moved into the reserve when the page starts
    model_calls: int = 0
    # Where the calls went, so a run shows which agent and which page spent them (summary() reports both).
    by_agent: dict[str, int] = field(default_factory=dict)
    by_page: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def spend(self, calls: int = 1) -> None:
        self.model_calls += calls

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self.started) / 60

    @property
    def working_calls(self) -> int:
        return max(0, self.max_model_calls - self.reserve_calls)

    @property
    def exhausted(self) -> bool:
        """No new agent work: the working budget is spent or time is up. The reserve is untouched by this."""
        return self.model_calls >= self.working_calls or self.elapsed_minutes >= self.max_minutes

    @property
    def hard_exhausted(self) -> bool:
        """Even the reserve is spent: closing stages stop too."""
        return self.model_calls >= self.max_model_calls

    def admit_page(self) -> bool:
        """Set one more page's review aside in the reserve before the page starts, so auditing it can't spend what
        reviewing it needs. False when the working budget can't spare that."""
        if self.model_calls + self.review_calls_per_page > self.working_calls:
            return False
        self.reserve_calls += self.review_calls_per_page
        return True

    def remaining(self) -> dict:
        return {"calls": max(0, self.working_calls - self.model_calls),
                "reserve_calls": max(0, self.max_model_calls - max(self.model_calls, self.working_calls)),
                "minutes": round(max(0.0, self.max_minutes - self.elapsed_minutes), 1), "pages": self.max_pages}


    def summary(self) -> dict:
        return {**{k: v for k, v in asdict(self).items() if k != "started"},
                "elapsed_minutes": round(self.elapsed_minutes, 1), "exhausted": self.exhausted}


# Agents that keep working on the reserve after the working budget is spent: the planner (so it can still call
# review_pages / write_summary / finish; its tools refuse NEW work), the reviewer and the summary writer (decision 6).
BUDGET_EXEMPT = ("Audit planner", "Reviewer", "Summary writer")


def charge_model_call(context: object | None, agent_name: str | None = None) -> None:
    """One model call against the run's budget. runtime.trim_input calls this before every LLM request of every agent
    (nested specialist and reviewer runs included), so it is the single place the budget policy lives:

    - no budget on the context (code mode, tests): nothing happens;
    - a working agent (lead, specialists, explorer, verifier) raises BudgetExhausted as soon as the working budget
      (max_model_calls - reserve_calls) or the time is spent, so one run can't overshoot by its whole turn cap;
    - the exempt agents (BUDGET_EXEMPT) raise only when even the reserve is gone (hard_exhausted).
    The raise is an AgentsException, so every stage degrades the way it does on any other agent error.
    """
    budget = getattr(context, "budget", None)
    if not isinstance(budget, Budget):
        return
    limit_hit = budget.hard_exhausted if agent_name in BUDGET_EXEMPT else budget.exhausted
    if limit_hit:
        raise BudgetExhausted(f"Model-call budget spent for {agent_name or 'this agent'} "
                              f"({budget.model_calls}/{budget.max_model_calls} calls, {budget.elapsed_minutes:.1f} min).")
    budget.spend()
    agent, page = agent_name or "unnamed", getattr(context, "current_url", None) or "no page"
    budget.by_agent[agent] = budget.by_agent.get(agent, 0) + 1
    budget.by_page[page] = budget.by_page.get(page, 0) + 1
