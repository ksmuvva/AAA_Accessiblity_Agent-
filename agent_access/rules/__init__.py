"""Rules: the evaluation layer. One rule per WCAG 2.2 success criterion (86), e.g. check_non_text_content (1.1.1).

    @wcag_tool("1.4.10", "check_reflow", domain="lead", tech=(PW,), uses=("reflow",))
    def check_reflow(ctx: Ctx) -> None: ...

A rule is a plain synchronous function over the raw data of the probes it declares in `uses` (agent_access/probes).
It is the only place verdicts are made: it records findings for its own criterion via ctx.fail / review /
passed / not_applicable. Rules never touch the browser, so they're fast and deterministic. agent_access/engine.py
collects the probes, runs the rules, and exposes each rule as an agent tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_access.catalogue.catalogue import load_catalogue
from agent_access.context import AuditContext
from agent_access.models import Finding, Status
from agent_access.probes import PROBES, ProbeResult

MAX_DETAIL_ITEMS = 12
MAX_SELECTORS = 15

AXE, PW, CCA, SR = "axe-core", "Playwright", "Colour Contrast Analyser method", "NVDA"


@dataclass
class WcagTool:
    criterion: str
    name: str
    title: str
    level: str
    domain: str              # which agent gets it: "lead", "forms", "media", or "navigation"
    tech: tuple[str, ...]
    uses: tuple[str, ...]    # probes whose data the rule reads
    judgement: bool          # True when an agent must judge something automation can't decide
    doc: str
    impl: Callable[["Ctx"], None]


REGISTRY: dict[str, WcagTool] = {}
# Every criterion the vendored axe-core maps a rule to (test_criterion_tools checks it against axe.getRules()). Their
# rules read axe, and engine.judge records axe's results for any of them whose own code didn't.
AXE_CRITERIA = frozenset({"1.1.1", "1.2.1", "1.2.2", "1.3.1", "1.3.4", "1.3.5", "1.4.1", "1.4.2", "1.4.3", "1.4.4",
                          "1.4.6", "1.4.12", "2.1.1", "2.1.3", "2.2.1", "2.2.2", "2.2.4", "2.4.1", "2.4.2", "2.4.4",
                          "2.4.9", "2.5.3", "2.5.8", "3.1.1", "3.1.2", "3.2.5", "3.3.2", "4.1.2"})


def wcag_tool(criterion: str, name: str, *, domain: str, tech: tuple[str, ...], uses: tuple[str, ...],
              judgement: bool = False):
    def register(impl):
        rule = load_catalogue().get(criterion)
        assert rule is not None, criterion
        all_uses = (*uses, "axe") if criterion in AXE_CRITERIA and "axe" not in uses else uses
        assert set(uses) <= set(PROBES), (name, set(uses) - set(PROBES))
        REGISTRY[criterion] = WcagTool(criterion, name, rule.title, rule.level, domain, tech, all_uses, judgement,
                                       (impl.__doc__ or "").strip(), impl)
        return impl
    return register


class Ctx:
    """What a rule works with: its probes' data and a findings builder for its criterion."""

    def __init__(self, audit: AuditContext, tool: WcagTool, probes: dict[str, ProbeResult]):
        self.audit, self.tool, self._probes = audit, tool, probes
        self.page_key = audit.current_url
        self.findings: list[Finding] = []
        self.details: dict = {}
        self.probe_errors = [f"{n}: {p.error}" for n, p in probes.items() if p.error]

    def data(self, probe: str) -> dict:
        if probe not in self._probes:
            raise KeyError(f"{self.tool.name} reads probe {probe!r} but doesn't declare it in uses=")
        return self._probes[probe].data

    def add(self, status: Status, description: str, selectors=(), evidence: str | None = None, source: str = "custom"):
        rule = self.audit.catalogue.get(self.tool.criterion)
        self.findings.append(Finding(
            rule_id=rule.rule_id, wcag_criterion=self.tool.criterion, status=status, source=source,
            check_id=self.tool.name, page_url=self.page_key, description=description,
            selectors=list(dict.fromkeys(s for s in selectors if s))[:MAX_SELECTORS],
            evidence=evidence[:600] if evidence else None,
        ))

    def fail(self, description, selectors=(), evidence=None):
        self.add(Status.FAIL, description, selectors, evidence)

    def review(self, description, selectors=(), evidence=None):
        self.add(Status.NEEDS_REVIEW, description, selectors, evidence)

    def passed(self, description, selectors=(), evidence=None):
        self.add(Status.PASS, description, selectors, evidence)

    def not_applicable(self, description, evidence=None):
        self.add(Status.NOT_APPLICABLE, description, evidence=evidence)

    def axe(self) -> None:
        """axe-core's results for this criterion: violation -> fail, incomplete -> needs review, pass -> pass."""
        for r in self.data("axe").get("rules", []):
            if self.tool.criterion in r["criteria"]:
                self.add(Status(r["outcome"]), f"{r['help']} ({r['elements']} element(s)) [axe: {r['id']}]",
                         r["selectors"], r["evidence"], source="axe")

    def detail(self, key: str, value) -> None:
        self.details[key] = value[:MAX_DETAIL_ITEMS] if isinstance(value, list) else value


def _load() -> None:
    # Importing the modules runs their @wcag_tool decorators.
    from agent_access.rules import operable, perceivable, robust, understandable  # noqa: F401


_load()

TOOLS_IN_ORDER: list[WcagTool] = sorted(REGISTRY.values(), key=lambda t: [int(x) for x in t.criterion.split(".")])
