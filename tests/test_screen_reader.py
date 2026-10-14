"""Emulated screen reader: parsing Playwright's aria_snapshot lines into NVDA-style speech."""

import asyncio

from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.context import AuditContext
from agent_access.probes.screen_reader import _emulated_observe, _match_snapshot_line, spoken_name, spoken_role


def test_yaml_quoted_snapshot_lines_are_parsed():
    # aria_snapshot quotes keys that contain ": " and doubles single quotes inside them.
    m = _match_snapshot_line("""- 'link "Citylights: your access to the city."':""")
    assert (m["role"], m["name"]) == ("link", "Citylights: your access to the city.")
    m = _match_snapshot_line("""  - 'button "It''s: saved" [pressed]'""")
    assert (m["role"], m["name"], m["attrs"]) == ("button", "It's: saved", " [pressed]")
    m = _match_snapshot_line('- link "plain":')
    assert (m["role"], m["name"]) == ("link", "plain")


def test_names_with_colons_are_announced_with_their_role():
    html = ('<html lang="en"><body><a id="logo" href="/">Citylights: your access to the city.</a>'
            '<h2>Tickets: buy online</h2><img src="x.png" alt="Logo: W3C"></body></html>')

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, html)
            return await _emulated_observe(audit, ["#logo"])

    result = asyncio.run(scenario())
    spoken = result["focus"][0]["spoken"]
    assert spoken_role(spoken) == "link" and spoken_name(spoken) == "Citylights: your access to the city."
    assert "heading, level 2, Tickets: buy online" in result["headings"]
    assert "Logo: W3C, graphic" in result["reading"]


def test_spoken_name_keeps_commas_in_the_name():
    assert spoken_name("Clara F., 15 months, link, focused") == "Clara F., 15 months"
    assert spoken_name("Send message, button, focused") == "Send message"
    assert spoken_name("combo box, QUICKMENU, focused, collapsed") == ""
    assert spoken_name("edit, focused") == ""
