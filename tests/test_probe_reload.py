"""Interactive probes reload the page between tests. Over HTTP that reload is a real navigation, which the probes'
own network_guard would block unless the reload is let through. Inline fixtures hide
this, because they're restored via about:blank, so these tests serve the page over HTTP.
"""

import asyncio
import functools
import http.server
import threading

from agent_access.agent_tools.browser_tools import open_url
from agent_access.context import AuditContext
from agent_access.probes import collect

PAGE = """<!DOCTYPE html>
<html lang="en"><head><title>Home</title></head><body>
<button id="menu-btn" aria-expanded="false" aria-controls="menu">Menu</button>
<ul id="menu" hidden><li><a href="other.html">Other</a></li></ul>
<div id="press" style="width: 120px; height: 40px">Press me</div>
<div id="panel" hidden>Panel opened on mousedown</div>
<script>
const btn = document.getElementById('menu-btn'), menu = document.getElementById('menu');
btn.addEventListener('click', () => {
  const open = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!open));
  menu.hidden = open;
});
document.addEventListener('keydown', (e) => {
  if (e.key === 's') { const p = document.createElement('p'); p.textContent = 'Shortcut fired'; document.body.appendChild(p); }
});
document.getElementById('press').addEventListener('mousedown', () => { document.getElementById('panel').hidden = false; });
</script></body></html>"""


def test_interactive_probes_reload_the_page_over_http(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await open_url(audit, url)
            results = {name: await collect(audit, name) for name in ("disclosures", "shortcuts", "pointer")}
            page = await audit.page()
            return results, page.url, await page.title(), await page.is_hidden("#panel")

    try:
        results, final_url, title, panel_hidden = asyncio.run(scenario())
    finally:
        server.shutdown()
    for name, result in results.items():
        assert result.error is None, (name, result.error)
    assert any(r["trigger"] == "#menu-btn" and r["opened_by"] == "Enter" for r in results["disclosures"].data["results"])
    assert [t["key"] for t in results["shortcuts"].data["triggered"]] == ["s"]
    assert any(r["selector"] == "#press" and r["acts_on_down"] for r in results["pointer"].data["results"])
    # Every probe put the page back: same URL, original content.
    assert final_url == url and title == "Home" and panel_hidden
