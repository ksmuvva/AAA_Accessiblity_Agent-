"""Phase 1 gate: nothing the audit does sends a write to a live site, on any path the review found.

A local site logs every request, a second server stands in for another site, and a raw WebSocket server counts the
data frames it receives. Only the two allowed writes may arrive: one the page sends while loading, before any
interaction, and one to the --allow-submit prefix.
"""

import asyncio
import base64
import hashlib
import http.server
import socket
import threading
import time

from agent_access.agent_tools.browser_tools import click, open_url
from agent_access.agent_tools.verify_tools import evaluate_script
from agent_access.context import AuditContext
from agent_access.probes.forms import submit_test
from agent_access.probes.interaction import disclosures

PAGE = """<!DOCTYPE html><html lang="en"><head><title>Gate</title></head><body>
<button id="later" onclick="setTimeout(() => fetch('/api/later', {method: 'POST'}), 1500)">Save later</button>
<button id="now" onclick="fetch('/api/now', {method: 'POST'})">Save</button>
<button id="beacon" onclick="navigator.sendBeacon('/api/beacon', 'x')">Beacon</button>
<button id="put" onclick="fetch('/api/put', {method: 'PUT'}); fetch('/api/del', {method: 'DELETE'})">Put</button>
<button id="popup" onclick="window.open('/popup')">Popup</button>
<a id="tab" href="/popup" target="_blank">Tab</a>
<button id="ws" onclick="const s = new WebSocket('ws://127.0.0.1:WS/'); s.onopen = () => s.send('hello')">Socket</button>
<button id="sw" onclick="navigator.serviceWorker && navigator.serviceWorker.register('/sw.js')">Worker</button>
<button id="shared" onclick="try { new SharedWorker('/shared.js') } catch (e) {}">Shared</button>
<button id="ok" onclick="fetch('/api/ok', {method: 'POST'})">Allowed</button>
<button id="del" aria-expanded="false" onclick="fetch('/api/account/delete')">Delete account</button>
<form id="cancel" action="/api/cancel"><button>Cancel my subscription</button></form>
<form id="post" method="post" action="/api/form"><input name="q"><button>Go</button></form>
<script>
fetch('/api/onload', {method: 'POST'});
addEventListener('pagehide', () => {
  fetch('/api/unload', {method: 'POST', keepalive: true});
  navigator.sendBeacon('/api/unload-beacon', 'x');
  if (window.fetchLater) fetchLater('/api/later-unload', {method: 'POST'});
});
</script></body></html>"""
SHARED = "fetch('/api/shared', {method: 'POST'});"
SW = "self.addEventListener('install', () => fetch('/api/sw', {method: 'POST'}));"


def _site(log: list, ws_port: int = 0):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            log.append((self.command, self.path))
            body = {"/": PAGE.replace("WS", str(ws_port)), "/popup": "<script>fetch('/api/popup', {method: 'POST'})</script>",
                    "/shared.js": SHARED, "/sw.js": SW}.get(self.path, "ok")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript" if self.path.endswith(".js") else "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        do_GET = do_POST = do_PUT = do_DELETE = _answer

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _websocket_server(frames: list) -> socket.socket:
    """Accepts WebSocket handshakes and records the opcode of every frame that arrives after one."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def serve(conn):
        head = conn.recv(4096).decode()
        key = next(line.split(":", 1)[1].strip() for line in head.split("\r\n") if line.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        while data := conn.recv(4096):
            frames.append(data[0] & 0x0F)

    threading.Thread(target=lambda: [threading.Thread(target=serve, args=(listener.accept()[0],), daemon=True).start()
                                     for _ in iter(int, 1)], daemon=True).start()
    return listener


def test_no_write_reaches_a_live_server():
    log, other, frames = [], [], []
    ws = _websocket_server(frames)
    site, elsewhere = _site(log, ws.getsockname()[1]), _site(other)
    base = f"http://127.0.0.1:{site.server_address[1]}"

    async def scenario():
        async with AuditContext(screen_reader="emulated", allow_interaction=True, allowed_writes=(f"{base}/api/ok",)) as audit:
            await open_url(audit, base + "/")
            for target in ("#later", "#now", "#beacon", "#put", "#popup", "#tab", "#ws", "#sw", "#shared", "#ok"):
                assert "error" not in await click(audit, target), target
            started = time.monotonic()
            await click(audit, "#post button")   # a POST navigation: blocked, and the click must not hang
            assert time.monotonic() - started < 5
            await evaluate_script(audit, "setTimeout(() => fetch('/api/script', {method: 'POST'}), 300)")
            await disclosures(audit, targets=["#del"])
            await submit_test(audit, "#cancel", "empty")
            refused = [await open_url(audit, u) for u in (f"http://127.0.0.1:{elsewhere.server_address[1]}/",
                                                           f"http://localhost:{site.server_address[1]}/", base + "/cart/empty")]
            await audit.restore_page()        # unloads the page: its pagehide writes must not get out either
            await asyncio.sleep(2.5)          # the delayed save and the script's timer fire meanwhile
        return refused

    try:
        refused = asyncio.run(scenario())
        time.sleep(1)
    finally:
        site.shutdown()
        elsewhere.shutdown()
        ws.close()
    writes = sorted({path for method, path in log if method != "GET"})
    assert writes == ["/api/ok", "/api/onload"], writes
    assert not [path for _, path in log if "delete" in path or "cancel" in path or path == "/popup"]
    assert all("Refused" in r.get("error", "") for r in refused) and other == []
    assert not {1, 2} & set(frames), frames   # no text or binary frame (a close frame is fine)
