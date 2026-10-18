"""Rules that had no fixture they fail on: each fails here from its own logic (or its axe mapping)."""

import asyncio

import pytest
from audit_helpers import found, judge

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.engine import run_rule
from agent_access.models import Status
from agent_access.rules import REGISTRY


def page(body: str, head: str = "") -> str:
    return f'<!doctype html><html lang="en"><head><title>Shop</title>{head}</head><body>{body}</body></html>'


FAILING = {
    "1.4.6": page('<main><h1>Shop</h1><p style="color:#767676">Grey text is 4.5:1, below the 7:1 for AAA.</p></main>'),
    "2.1.3": page('<main><h1>Shop</h1><button onfocus="this.blur()">Buy</button><a href="#a">After</a></main>'),
    "2.2.1": page("<main><h1>Shop</h1><p>Refreshes.</p></main>", '<meta http-equiv="refresh" content="7200">'),
    "2.4.1": page('<div><a href="/a">Home</a> <a href="/b">Shop</a></div><div><p>Offers this week.</p></div>'),
    "2.4.4": page('<main><h1>Shop</h1><a href="/cart"><img src="cart.png"></a></main>'),
    "2.5.3": page('<main><h1>Shop</h1><button aria-label="Submit form">Send</button></main>'),
    "3.2.5": page("<main><h1>Shop</h1><p>Refreshes.</p></main>", '<meta http-equiv="refresh" content="7200">'),
}


@pytest.mark.parametrize("criterion", sorted(FAILING))
def test_rule_fails_on_its_fixture(criterion):
    findings = found(judge(FAILING[criterion], criterion), criterion)
    assert findings.get(Status.FAIL), (criterion, {s.value: [f.description for f in fs] for s, fs in findings.items()})


def test_consistent_help_fails_when_the_order_changes():
    help_links = '<header><a href="/help">Help</a> <a href="/contact">Contact us</a></header><main><h1>{}</h1></main>'

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            for key, body in (("file:a.html", help_links), ("file:b.html", help_links.replace(
                    '<a href="/help">Help</a> <a href="/contact">Contact us</a>',
                    '<a href="/contact">Contact us</a> <a href="/help">Help</a>'))):
                await load_html_content(audit, page(body.format(key)), key)
                await run_rule(audit, REGISTRY["3.2.6"])
            return audit
    audit = asyncio.run(scenario())
    assert {f.status for f in audit.findings_for("file:b.html") if f.wcag_criterion == "3.2.6"} == {Status.FAIL}
