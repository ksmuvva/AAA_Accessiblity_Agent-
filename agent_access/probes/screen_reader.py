"""Screen-reader probe: what a screen-reader user hears on the page.

Two backends with the same output:
- NVDA: real NVDA speech via Guidepup (nvda_bridge/bridge.mjs). Needs Windows, NVDA installed with
  `guidepup install nvda`, and a visible (headed) browser window, because NVDA reads the foreground window.
- emulated: built from Chromium's accessibility tree (the same data NVDA consumes), for CI/headless runs.

Output: {"backend", "reading" (browse-mode lines in order), "headings", "focus" ([{selector, spoken}])}.
Live-region announcements are not captured from NVDA (Guidepup only logs speech caused by its own
commands), so status messages (4.1.3) come from DOM observation in the form tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import uuid
from pathlib import Path

from agent_access.probes import collect, probe

BRIDGE = Path(__file__).resolve().parents[2] / "nvda_bridge" / "bridge.mjs"
GUIDEPUP_NVDA = Path(os.environ.get("LOCALAPPDATA", "")) / "guidepup" / "nvda"
MAX_READING_LINES = 80
MAX_HEADINGS = 40
MAX_FOCUS = 40
COMMAND_TIMEOUT_S = 60
# NVDA (portable, plus Guidepup's add-on connecting) takes about 80 s to start on this machine.
START_TIMEOUT_S = 400  # covers the bridge's 3 start attempts


def nvda_available() -> bool:
    return platform.system() == "Windows" and BRIDGE.exists() and GUIDEPUP_NVDA.exists() and \
        (BRIDGE.parent / "node_modules" / "@guidepup" / "guidepup").exists()


class NVDASession:
    """One NVDA process for the whole audit, driven through the Node bridge."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self.version = "NVDA (Guidepup)"

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            "node", str(BRIDGE), cwd=str(BRIDGE.parent),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        await self.cmd("start", _timeout=START_TIMEOUT_S)
        await asyncio.sleep(4)  # NVDA takes a few seconds to come up and open its speech viewer.
        versions = sorted(p.name for p in (GUIDEPUP_NVDA / "all").iterdir()) if (GUIDEPUP_NVDA / "all").exists() else []
        if versions:
            self.version = f"NVDA {versions[-1]} (Guidepup)"

    async def cmd(self, name: str, _timeout: float = COMMAND_TIMEOUT_S, **args):
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("NVDA bridge is not running")
        self._id += 1
        self.proc.stdin.write((json.dumps({"id": self._id, "cmd": name, "args": args}) + "\n").encode())
        await self.proc.stdin.drain()
        line = await asyncio.wait_for(self.proc.stdout.readline(), _timeout)
        if not line:
            raise RuntimeError("NVDA bridge exited")
        msg = json.loads(line)
        if not msg.get("ok"):
            raise RuntimeError(msg.get("error", "NVDA command failed"))
        return msg.get("result")

    async def stop(self) -> None:
        if self.proc is None:
            return
        try:
            await self.cmd("stop", _timeout=30)
        except Exception:
            pass
        finally:
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except Exception:
                self.proc.kill()
            self.proc = None


def use_nvda(audit) -> bool:
    if audit.screen_reader == "emulated":
        return False
    if audit.screen_reader == "nvda":
        return True
    return nvda_available() and not audit.headless


async def ensure_nvda(audit) -> NVDASession:
    """Start NVDA once per audit. Call it early (before the browser is busy) to make the slow start reliable."""
    if audit.nvda is None:
        session = NVDASession()
        try:
            await session.start()
        except Exception:
            await session.stop()
            raise
        audit.nvda = session
    return audit.nvda


async def _nvda_observe(audit, focus_selectors: list[str]) -> dict:
    nvda = await ensure_nvda(audit)
    page = await audit.page()
    # NVDA reads the foreground window; give it a unique title so the bridge can bring it forward.
    original_title = await page.title()
    marker = f"a11y-audit-{uuid.uuid4().hex[:6]}"
    await page.evaluate("(t) => { document.title = t; }", marker)
    try:
        # NVDA must actually be reading the browser. Windows' focus-stealing protection can stop a program
        # launched from a background terminal from raising its window; then NVDA returns empty speech (or
        # reads another window, e.g. "Secure Desktop" while the machine is locked), which must never be
        # mistaken for the page. NVDA's window title (NVDA+T) must contain the marker.
        top, on_page = "", False
        for _ in range(2):
            await page.bring_to_front()
            await nvda.cmd("activate", executablePath=audit.executable_path, windowTitle=marker)
            await asyncio.sleep(1.5)
            title = await nvda.cmd("perform", command="reportTitle") or ""
            on_page = marker in title
            top = await nvda.cmd("press", key="Control+Home") if on_page else ""
            if top and top.strip():
                break
        if not (on_page and top and top.strip()):
            raise RuntimeError("NVDA isn't reading the browser window (Windows didn't let it come to the foreground; "
                               "run from a normal terminal and don't use the machine during the NVDA step)")

        # NVDA repeats the last line at the end of the document. Identical lines also occur mid-page (a run of
        # unnamed images all read "graphic"), so read the real last line first and only stop on that.
        bottom = (await nvda.cmd("press", key="Control+End") or "").strip()
        top = await nvda.cmd("press", key="Control+Home") or top
        reading = [top]
        for _ in range(MAX_READING_LINES):
            line = await nvda.cmd("next")
            if bottom and line and line.strip() == bottom and reading[-1] and reading[-1].strip() == bottom:
                break
            if not bottom and line and len(reading) >= 2 and line == reading[-1] == reading[-2]:
                break
            reading.append(line)
        if not bottom:
            while len(reading) > 1 and reading[-1] == reading[-2]:
                reading.pop()

        await nvda.cmd("press", key="Control+Home")
        headings: list[str] = []
        for _ in range(MAX_HEADINGS):
            h = await nvda.cmd("nextHeading")
            if not h or (headings and h == headings[-1]):
                break
            headings.append(h)

        focus = []
        for sel in focus_selectors[:MAX_FOCUS]:
            try:
                await page.locator(sel).first.focus(timeout=2000)
            except Exception:
                continue
            await asyncio.sleep(0.3)
            focus.append({"selector": sel, "spoken": await nvda.cmd("perform", command="reportCurrentFocus")})
        if focus and not any((f["spoken"] or "").strip() for f in focus):
            raise RuntimeError("NVDA returned no speech for any focused element")
    finally:
        await page.evaluate("(t) => { document.title = t; }", original_title)
    return {"backend": "nvda", "engine": nvda.version, "reading": [r for r in reading if r and r.strip("￼ ")],
            "headings": headings, "focus": focus}


# Emulated speech, phrased like NVDA: "name, role, states".
_ROLE_WORDS = {"textbox": "edit", "img": "graphic", "checkbox": "check box", "radio": "radio button",
               "combobox": "combo box", "listbox": "list", "menuitem": "menu item", "tab": "tab",
               "searchbox": "edit", "spinbutton": "spin button", "slider": "slider", "switch": "toggle button"}
_SNAPSHOT_LINE = re.compile(r'^\s*-\s+(?P<role>[a-z]+)(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?:\s*\[[^\]]*\])*)(?::\s*(?P<text>.*))?$')
# aria_snapshot YAML-quotes a key that contains ": " or other YAML syntax: - 'link "Note: it''s here"':
_QUOTED_KEY = re.compile(r"^(?P<dash>\s*-\s+)'(?P<key>(?:[^']|'')*)'(?P<rest>.*)$")


def _match_snapshot_line(raw: str) -> re.Match | None:
    quoted = _QUOTED_KEY.match(raw)
    if quoted:
        raw = quoted["dash"] + quoted["key"].replace("''", "'") + quoted["rest"]
    return _SNAPSHOT_LINE.match(raw)


def _speak(m: re.Match, text: str | None, focused: bool = False) -> str:
    """NVDA-style speech for one parsed snapshot line: "name, role, states" (headings: "heading, level N, name")."""
    role, name, attrs = m["role"], (m["name"] or "").replace('\\"', '"'), m["attrs"]
    if role == "text":
        return (text or name or "").strip()
    states = []
    for a in re.findall(r"\[([^\]]*)\]", attrs or ""):
        key, _, val = a.partition("=")
        if key == "level":
            continue
        if key == "expanded":
            states.append("expanded" if val == "true" else "collapsed")
        elif key in ("checked", "pressed", "selected") and val in ("true", ""):
            states.append(key)
        elif key in ("disabled", "required", "invalid"):
            states.append("invalid entry" if key == "invalid" else key)
    level = re.search(r"\[level=(\d+)\]", attrs or "")
    word = f"heading, level {level.group(1)}" if role == "heading" and level else _ROLE_WORDS.get(role, role)
    parts = [word, name] if role == "heading" else [name, word]
    if focused:
        parts.append("focused")
    return ", ".join(p for p in parts + states if p) + (f", {text.strip()}" if text and text.strip() else "")


def reading_items(snapshot: str) -> list[tuple[str, str, str]]:
    """(role, name, spoken) for each line the emulated reading order speaks, from an aria snapshot of the body."""
    items = []
    for raw in snapshot.splitlines():
        m = _match_snapshot_line(raw)
        if not m or m.group("role") in ("generic", "group", "list", "listitem", "paragraph", "main", "navigation",
                                         "banner", "contentinfo", "region", "form", "row", "rowgroup", "table", "cell"):
            if m and m.group("role") in ("paragraph", "listitem", "cell") and m.group("text"):
                items.append(("text", "", m.group("text").strip()))
            continue
        if spoken := _speak(m, m.group("text")):
            items.append((m.group("role"), (m["name"] or "").replace('\\"', '"'), spoken))
    return items


async def _emulated_observe(audit, focus_selectors: list[str]) -> dict:
    page = await audit.page()
    items = reading_items(await page.locator("body").aria_snapshot())
    reading = [spoken for _, _, spoken in items]
    headings = [spoken for role, _, spoken in items if role == "heading"]
    focus = []
    for sel in focus_selectors[:MAX_FOCUS]:
        try:
            snap = (await page.locator(sel).first.aria_snapshot(timeout=2000)).splitlines()[0]
        except Exception:
            continue
        m = _match_snapshot_line(snap)
        spoken = _speak(m, None, focused=True) if m else snap
        focus.append({"selector": sel, "spoken": spoken})
    return {"backend": "emulated", "engine": "Chromium accessibility tree (NVDA-style phrasing)",
            "reading": reading[:MAX_READING_LINES * 2], "headings": headings, "focus": focus}


@probe("screen_reader")
async def screen_reader(audit) -> dict:
    """Reading order, headings, and the announcement for every Tab stop found by the keyboard probe."""
    stops = (await collect(audit, "keyboard")).data.get("stops", [])
    selectors = [s["selector"] for s in stops if s["tag"] != "iframe"]
    if use_nvda(audit):
        try:
            result = await _nvda_observe(audit, selectors)
        except Exception as exc:
            result = await _emulated_observe(audit, selectors)
            result["note"] = f"NVDA failed ({type(exc).__name__}: {str(exc)[:120]}); used the emulated backend."
    else:
        result = await _emulated_observe(audit, selectors)
        if audit.screen_reader == "auto" and nvda_available() and audit.headless:
            result["note"] = "NVDA is installed but the browser is headless; run with --headed to use real NVDA."
    audit.engines["screen reader"] = result["engine"] + (f" - {result['note']}" if result.get("note") else "")
    return result


WIDGET_ROLE_WORDS = ("button", "link", "edit", "check box", "radio button", "combo box", "list", "menu item", "tab",
                     "slider", "spin button", "toggle button", "graphic", "document", "frame", "tree view", "grid",
                     "menu button", "switch", "search", "text", "split button", "dialog", "list item", "option")


# A state NVDA may say in the same phrase as the role ("visited link"). Only these: a name that ends in a role word
# ("Advanced search", "Help menu item") is a name, not a role.
_STATES = re.compile(r"^(?:(?:visited|clickable|same page|unlabeled|unlabelled|selected|not selected|current page|"
                     r"checked|not checked|pressed|not pressed|expanded|collapsed|read only|required|invalid entry) )+")


def _is_role_word(part: str) -> bool:
    return _STATES.sub("", part) in WIDGET_ROLE_WORDS


def spoken_role(spoken: str) -> str | None:
    """The control role in an announcement ("Send message, button, focused" -> "button")."""
    for part in (p.strip() for p in (spoken or "").split(",")):
        if _is_role_word(part):
            return part
    return None


def spoken_name(spoken: str) -> str:
    """The accessible name in an announcement: everything before the role. NVDA says the name first, and names can
    contain commas ("Clara F., 15 months, link" -> "Clara F., 15 months"); if it starts with the role, there's no name."""
    parts = [p.strip() for p in (spoken or "").split(",")]
    for i, part in enumerate(parts):
        if _is_role_word(part) or part == "focused":
            return ", ".join(parts[:i])
    return parts[0]
