"""Shared test helpers: load a fixture, run rules (which collect the probes they need), and read the results."""

import asyncio
from pathlib import Path

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.engine import run_rule
from agent_access.models import Status
from agent_access.rules import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def judge_then(html_or_fixture: str, *criteria: str, then=None) -> tuple[AuditContext, object]:
    """Load a fixture (by file name) or raw HTML, run the rules for `criteria`, then `await then(audit)` while the
    browser is still open. Returns (audit, what `then` returned)."""

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            is_file = html_or_fixture.endswith(".html")
            await load_html_content(audit, fixture_html(html_or_fixture) if is_file else html_or_fixture,
                                    html_or_fixture if is_file else "about:inline-html")
            for c in criteria:
                await run_rule(audit, REGISTRY[c])
            return audit, (await then(audit) if then else None)

    return asyncio.run(scenario())


def judge(html_or_fixture: str, *criteria: str) -> AuditContext:
    """Load a fixture (by file name) or raw HTML and run the rules for `criteria`."""
    return judge_then(html_or_fixture, *criteria)[0]


def data(audit: AuditContext, probe: str) -> dict:
    """A probe's cached data for the loaded page."""
    return audit.observations[audit.current_url][probe].data


def statuses(audit: AuditContext, criterion: str) -> set[Status]:
    return {f.status for f in audit.findings if f.wcag_criterion == criterion}


def found(audit: AuditContext, criterion: str) -> dict[Status, list]:
    out: dict[Status, list] = {}
    for f in audit.findings:
        if f.wcag_criterion == criterion:
            out.setdefault(f.status, []).append(f)
    return out
