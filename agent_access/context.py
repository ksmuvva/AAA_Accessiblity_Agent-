"""Shared state passed to every tool via the Agents SDK run context.

One AuditContext (the root) owns the browser. `fork(owner)` makes a child context that shares every result with the
root (findings, probe cache, tool runs, ...) but drives its own browser page under its own lock, so several agents can
work on the same page, or on different pages, at the same time (`run_parallel`, `run_agents`).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Iterator, Literal, TypeVar
from urllib.parse import urlparse

from agents import RunContextWrapper
from playwright.async_api import (Browser, BrowserContext, Page, Playwright, Request, Route, WebSocketRoute,
                                  async_playwright)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agent_access.catalogue.catalogue import LEVEL_ORDER, Catalogue, Rule, load_catalogue
from agent_access.intent import destroys
from agent_access.jsassets import js
from agent_access.models import Finding

if TYPE_CHECKING:
    from agents import Agent, RunConfig, RunResult

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
NAVIGATION_TIMEOUT_MS = 30_000
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# Installed before any page script runs: logs listeners, timers, and window.open (read by the instrumentation probe).
INSTRUMENT_JS = js("page/instrument")
SAFETY_JS = js("page/safety")
# GET requests that can act (a page load or a script call); an image or a stylesheet with "delete" in its name can't.
ACTING_TYPES = ("document", "fetch", "xhr", "ping", "eventsource", "other")
# Probe-level guard tripwires (no action) stop being stored past this many; probes block every form post they
# trigger. Records of an agent's blocked actions are never capped: they are what the report shows.
MAX_TRIPWIRES = 500

T = TypeVar("T")
# How the lead reaches specialists (G2 imports this): SDK handoffs, or the delegate tools of code mode.
Delegation = Literal["handoffs", "tools"]


def no_page() -> dict:
    return {"error": "No page is loaded yet."}


async def call_tool(ctx: RunContextWrapper["AuditContext"], fn: Callable[..., dict | Awaitable[dict]], *args) -> str:
    """The body of every browser-facing agent tool: needs a loaded page, holds the lock, returns JSON.
    `fn(audit, *args)` may be sync or async.

    Delegate tools must not use this: the specialist's own tools take the lock, so holding it would deadlock.
    """
    audit = ctx.context
    if not audit.has_page():
        return json.dumps(no_page())
    async with audit.lock:
        result = fn(audit, *args)
        return json.dumps(await result if inspect.isawaitable(result) else result)


def is_state_key(page_key: str) -> bool:
    return " [" in page_key


def base_page_key(page_key: str) -> str:
    """The page a state key belongs to: "https://x/cart [step 2]" -> "https://x/cart" (a plain key is returned as is)."""
    return page_key.split(" [", 1)[0]


@dataclass
class AuditContext:
    """Holds the browser session and everything collected during one run.

    Use as an async context manager so the browser is always closed:

        async with AuditContext() as audit:
            await Runner.run(agent, prompt, context=audit)

    `audit.fork(owner)` gives a child context for a concurrent agent: it shares every result with this one and
    owns only its browser page, lock and page cursor (see _OWN below).
    """

    headless: bool = True
    # Free-form interaction tools (click/fill/press) can change state on real sites, so they are opt-in.
    allow_interaction: bool = False
    # URL prefixes that free-form interaction may send non-GET requests to (e.g. a test checkout's step API).
    # Everything else is blocked while those tools act, so a click can't submit, buy, or delete for real.
    allowed_writes: tuple[str, ...] = ()
    # Free-form interaction may follow links to other hosts only when set (network_guard blocks them otherwise).
    allow_offsite: bool = False
    # Send agent traces to the OpenAI tracing dashboard (they include page content and screenshots): opt-in.
    tracing: bool = False
    output_dir: Path = Path("reports")
    # A saved browser session (cookies and storage, from --save-login) to audit pages behind a sign-in.
    storage_state: Path | None = None
    # Conformance target: "A", "AA", or "AAA". Criteria above it are still tested and reported separately.
    target_level: str = "AA"
    # Screen reader for the screen-reader engine: "nvda" (real NVDA via Guidepup; needs a visible browser),
    # "emulated" (Chromium accessibility tree), or "auto" (NVDA when installed and the browser is visible).
    screen_reader: str = "auto"
    # "eager": every probe and rule runs on every page before an agent sees it; "lazy": agents choose (G6).
    measurement: str = "eager"
    # How the lead reaches specialists: SDK "handoffs", or the delegate "tools" (code mode, --delegation tools; G2).
    delegation: Delegation = "handoffs"
    # The screen reader user agent (team/sr_user.py, --sr-agent): None is off; a set is on, holding the pages it used.
    sr_agent: set[str] | None = None
    catalogue: Catalogue = field(default_factory=load_catalogue)
    findings: list[Finding] = field(default_factory=list)
    current_url: str | None = None
    # Every page audited in this run, in visit order.
    pages: list[str] = field(default_factory=list)
    # Raw HTML for pages loaded with load_html, so they can be restored after interaction tests.
    inline_html: dict[str, str] = field(default_factory=dict)
    # Tests already tried per page (tool or specialist names), used by the planning loop.
    attempted: dict[str, set[str]] = field(default_factory=dict)
    # Navigation structure per page, used to compare consistency across pages.
    nav_snapshots: dict[str, dict] = field(default_factory=dict)
    # Pages that have had the full automated sweep, and a log of every rule run (agent_access/engine.py).
    swept: set[str] = field(default_factory=set)
    tool_runs: list[dict] = field(default_factory=list)
    # Every agent-written script run with evaluate_js (id "S1", "S2", ...), so a judgement can cite its evidence and
    # the reviewer can re-run it.
    scripts: list[dict] = field(default_factory=list)
    # Probe results cached per page (agent_access/probes), so the 86 rules share one run of each probe.
    observations: dict[str, dict] = field(default_factory=dict)
    # Each rule's latest details per page ({page: {rule name: details}}), served by get_criterion.
    rule_details: dict[str, dict] = field(default_factory=dict)
    # Engine versions and modes used in this run, for the report.
    engines: dict[str, str] = field(default_factory=dict)
    # Coverage gaps the code detected (a page the lead left open, a specialist that skipped criteria).
    warnings: list[str] = field(default_factory=list)
    # Pages changed by free-form interaction since they were loaded: their cached engine results are stale.
    # Per context: a fork's page can be changed while the root's copy is still pristine.
    dirty: set[str] = field(default_factory=set)
    # The interaction steps (click, fill, press) taken on this context's page since it was loaded fresh as
    # `steps_origin` (a plain page key), so a state labelled after them can be rebuilt by replaying them. Per context,
    # like the page. `state_paths` (shared) maps each rebuildable state key to {"origin", "steps", "fingerprint"}.
    steps: list[dict] = field(default_factory=list)
    steps_origin: str | None = None
    state_paths: dict[str, dict] = field(default_factory=dict)
    # Agentic mode (see .claude/AGENTIC_PLAN.md): the run's Budget (G1), the planner's decisions (G1), guard tripwires
    # recorded by network_guard and the interaction tools (G5), the handoff log (G2), and the site/lesson memory (G4).
    budget: object | None = None
    plan_log: list[dict] = field(default_factory=list)
    tripwires: list[dict] = field(default_factory=list)
    handoffs: list[dict] = field(default_factory=list)
    memory: object | None = field(default=None, repr=False)
    # Identifies this run (memory scopes host-less pages and caps notes by it); forks share their root's.
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    nvda: object | None = field(default=None, repr=False)
    # Model tools may be called in parallel; browser tools must not drive the page concurrently.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # None on the root context; a fork carries the name it was forked under (e.g. "Forms specialist").
    owner: str | None = None

    _playwright: Playwright | None = field(default=None, init=False, repr=False)
    _browser: Browser | None = field(default=None, init=False, repr=False)
    _context: BrowserContext | None = field(default=None, init=False, repr=False)
    _page: Page | None = field(default=None, init=False, repr=False)
    # True while restore_page navigates, so an active network_guard lets that one navigation through.
    _restoring: bool = field(default=False, init=False, repr=False)
    # This context's page as the run-wide route sees it (see network_guard): the guard windows open on it, the last one
    # closed, and `fresh`, true from our own load of the page until the next window (only then may it write on its own).
    _windows: list = field(default_factory=list, init=False, repr=False)
    _last_window: dict | None = field(default=None, init=False, repr=False)
    fresh: bool = field(default=False, init=False, repr=False)
    _by_page: dict = field(default_factory=dict, init=False, repr=False)  # on the root: every page we opened -> its context
    # Forks by owner name (kept on the root), and one lock per (page key, probe) so concurrent forks measure once.
    _forks: dict[str, "AuditContext"] = field(default_factory=dict, init=False, repr=False)
    _probe_locks: dict[tuple, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    _root: "AuditContext | None" = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> AuditContext:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ------------------------------------------------------------------ browser pages and forks

    async def page(self) -> Page:
        """Return this context's page, launching the browser on first use (a fork's page: `fork(owner).page()`)."""
        if self._page is None:
            if self._root is not None:
                await self._root.page()  # the root launches the browser; a fork opens a page in its context
                self._page = await self._context.new_page()
            else:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
                self.engines["browser"] = f"Chromium {self._browser.version}"   # the accessibility support baseline
                # bypass_csp lets us inject axe-core into pages with strict Content-Security-Policy.
                self._context = await self._browser.new_context(
                    viewport=DEFAULT_VIEWPORT, bypass_csp=True, service_workers="block",
                    storage_state=str(self.storage_state) if self.storage_state else None)
                await self._context.add_init_script(INSTRUMENT_JS)
                await self._context.add_init_script(SAFETY_JS)
                await self._context.route("**/*", self._route)
                self._page = await self._context.new_page()
            self._by_page[self._page] = self
            await self._page.route_web_socket("**/*", self._websocket)
        return self._page

    def fork(self, owner: str) -> AuditContext:
        """A child context for a concurrent agent, named `owner` (idempotent per name).

        It shares every result with the root (reads and writes of shared attributes go to the root) and owns its own
        browser page (same browser context, so cookies and storage are shared), lock, page cursor and dirty set. Its
        page is opened by its first page() or restore_page(); until then has_page() is False. A fork's fork is its own
        context too, named by its path ("lead A > Forms specialist"), so two parallel leads never share a specialist's.
        """
        if self._root is not None:
            return self._root.fork(f"{self.owner} > {owner}")
        if owner not in self._forks:
            child = _ForkedContext.__new__(_ForkedContext)
            own = {"_root": self, "owner": owner, "current_url": None, "dirty": set(), "lock": asyncio.Lock(),
                   "_page": None, "_restoring": False, "steps": [], "steps_origin": None,
                   "_windows": [], "_last_window": None, "fresh": False}
            for name, value in own.items():
                object.__setattr__(child, name, value)
            self._forks[owner] = child
        return self._forks[owner]

    def can_fork(self) -> bool:
        """Parallel work drives several pages at once. Real NVDA reads the foreground window, so with it only one
        page may be driven, and callers fall back to sequential work on the root."""
        return self.nvda is None and self.screen_reader != "nvda"

    def probe_lock(self, page_key: str | None, probe: str) -> asyncio.Lock:
        """The lock concurrent collect() calls for one (page, probe) share, so the probe runs once."""
        return self._probe_locks.setdefault((page_key, probe), asyncio.Lock())

    def target_rules(self) -> list[Rule]:
        return self.catalogue.up_to_level(self.target_level)

    def beyond_target_rules(self) -> list[Rule]:
        return [r for r in self.catalogue.rules if LEVEL_ORDER[r.level] > LEVEL_ORDER[self.target_level]]

    @property
    def executable_path(self) -> str | None:
        return self._playwright.chromium.executable_path if self._playwright else None

    def has_page(self) -> bool:
        return self._page is not None and self.current_url is not None

    def set_current(self, page_url: str) -> None:
        self.current_url = page_url
        if page_url not in self.pages:
            self.pages.append(page_url)

    @contextmanager
    def at_page(self, page_key: str | None) -> Iterator[None]:
        """Point the page cursor (current_url) at `page_key` for a block, and always put it back afterwards.

        Engines and tools record findings under current_url, and some (restore_page, interaction) move it.
        """
        previous = self.current_url
        self.current_url = page_key
        try:
            yield
        finally:
            self.current_url = previous

    def mark_attempted(self, test_id: str) -> None:
        if self.current_url is not None:
            self.attempted.setdefault(self.current_url, set()).add(test_id)

    def attempted_on(self, page_url: str) -> set[str]:
        return self.attempted.get(page_url, set())

    def add_findings(self, findings: list[Finding]) -> None:
        self.findings.extend(findings)

    def replace_findings(
        self, page_url: str, source: str | None, findings: list[Finding], check_id: str | None = None
    ) -> None:
        """Drop earlier findings from `source` (any source if None; optionally one `check_id`) for this page,
        then add new ones. Re-running a scan, check, or criterion tool therefore replaces its results.
        """

        def is_stale(f: Finding) -> bool:
            return (f.page_url == page_url and (source is None or f.source == source)
                    and (check_id is None or f.check_id == check_id))

        self.findings = [f for f in self.findings if not is_stale(f)]
        self.findings.extend(findings)

    def findings_for(self, page_url: str) -> list[Finding]:
        return [f for f in self.findings if f.page_url == page_url]

    def can_restore(self, page_key: str | None = None) -> bool:
        """Process states (labelled with label_page_state) can't be recreated by reloading."""
        key = page_key or self.current_url
        return key is not None and not is_state_key(key)

    async def restore_page(self, page_key: str | None = None) -> bool:
        """Reload a page to undo side effects of interaction tests. Returns False if it can't be restored."""
        key = page_key or self.current_url
        if key is None or is_state_key(key):
            return False
        page = await self.page()
        # Probes may restore the page while a network_guard window blocks main-frame navigations; the flag lets this
        # reload through.
        self._restoring = True
        try:
            if key in self.inline_html:
                await self.set_inline_content(self.inline_html[key])
            elif key.startswith(("http://", "https://")):
                await self._reload(page, key)
            else:
                return False
        finally:
            self._restoring = False
        self.current_url = key
        self.dirty.discard(key)
        self.start_steps(key)
        return True

    def start_steps(self, page_key: str) -> None:
        """The page was just loaded fresh as `page_key`: interaction steps from here on can be replayed from it."""
        self.steps, self.steps_origin = [], page_key

    async def restore_state(self, state_key: str) -> bool:
        """Rebuild a labelled state: reload the page it started from, replay the steps that reached it, and check the
        page matches what was labelled (its fingerprint). False when the state has no recorded path or the replay
        reached a different page; the browser is then left on the changed origin page."""
        from agent_access.agent_tools.browser_tools import page_fingerprint, replay_steps  # it imports this module
        path = self.state_paths.get(state_key)
        if path is None or not await self.restore_page(path["origin"]):
            return False
        if not await replay_steps(self, path["steps"]) or await page_fingerprint(self) != path["fingerprint"]:
            self.dirty.add(path["origin"])
            return False
        self.steps, self.current_url = list(path["steps"]), state_key
        self.dirty.discard(state_key)
        if path["steps"]:
            self.dirty.add(path["origin"])   # the browser shows the state now, not the origin page
        return True

    async def return_to(self, page_key: str) -> bool:
        """Load `page_key` as it was audited: restore_page for a page, restore_state for a labelled state."""
        return await (self.restore_state(page_key) if is_state_key(page_key) else self.restore_page(page_key))

    async def _reload(self, page: Page, url: str) -> None:
        """goto with the two recoveries real sites need: a load timeout is retried waiting only for the document
        (heavy sites keep loading ads and trackers), and net::ERR_ABORTED (a client-side navigation started by the
        probe superseded our reload, seen on angular.dev and vuejs.org) is retried once."""
        self.fresh = True
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)
                return
            except PlaywrightTimeoutError:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
                return
            except PlaywrightError as exc:
                if "ERR_ABORTED" not in str(exc) or attempt:
                    raise
                await page.wait_for_timeout(500)  # let the competing navigation finish before retrying

    async def set_inline_content(self, html: str) -> None:
        """Load raw HTML into a fresh document.

        set_content alone reuses the current window, so re-running a page's scripts fails on
        redeclared top-level `const`/`let` names and its event listeners never attach. Navigating to
        about:blank first gives the scripts a fresh global scope.
        """
        page = await self.page()
        self.fresh = True
        await page.goto("about:blank")
        await page.set_content(html, wait_until="load", timeout=NAVIGATION_TIMEOUT_MS)

    def _trip(self, kind: str, detail: str, *, agent_action: bool, tool: str | None, offsite: bool = False) -> None:
        """Record a blocked request in tripwires (same keys as agent_access.tripwires.record_tripwire).

        A probe's guard blocks form posts on purpose: those records have no "action" and stay out of the report
        (tripwire_report shows records with one). A block of something an agent did (free-form interaction, its own
        script) is a real safety event: kind "write_blocked" / "offsite_navigation", action "blocked".
        """
        if agent_action:
            kind = "offsite_navigation" if offsite else "write_blocked"
        elif sum(1 for t in self.tripwires if t.get("action") is None) >= MAX_TRIPWIRES:
            return  # only the expected probe blocks are capped; an agent's blocked action is always kept
        self.tripwires.append({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "page_url": self.current_url, "owner": self.owner, "kind": kind,
                               "detail": detail + (" (offsite)" if offsite else ""),
                               "action": "blocked" if agent_action else None, "agent": None, "tool": tool})

    @asynccontextmanager
    async def network_guard(
        self, *, allow_navigation: bool = False, allow_writes: tuple[str, ...] = (),
        source: Literal["probe", "agent"] = "probe", tool: str | None = None,
    ) -> AsyncIterator[list[str]]:
        """Open a window of rules on this context's page while a test or an agent interacts with it.

        Every request of the run goes through one route on the browser context (`_route`), judged by its page's open
        window. By default a window blocks every non-GET request (form posts, API writes), every GET whose path says it
        acts destructively, and every main-frame navigation. Free-form interaction passes allow_navigation=True (GET
        navigations go through, but only to the page's own host unless allow_offsite is set) and the user's
        allow_writes URL prefixes. The rules stay in force after the window closes, until the page is loaded fresh
        again: a delayed save, a timer's fetch or a socket message a click set off is still blocked.
        Yields the list of blocked requests, which tests use to tell that a form *tried* to submit. Every block is also
        recorded in `tripwires`: with source="agent" (`tool` names the caller) as a reportable safety event with an
        action; with the default source="probe", or after the window closed, as an expected block with none.
        """
        page = await self.page()
        blocked: list[str] = []
        window = {"nav": allow_navigation, "writes": allow_writes, "agent": source == "agent", "tool": tool,
                  "blocked": blocked, "host": urlparse(page.url).netloc}
        self._windows.append(window)
        self.fresh = False
        try:
            yield blocked
        finally:
            self._windows.remove(window)
            self._last_window = window

    async def _route(self, route: Route, request: Request) -> None:
        """Every request of the run: judged by the window of the page that sent it."""
        try:
            owner = self._by_page.get(request.frame.page)
        except Exception:  # a worker's request has no frame: it may read, nothing more
            if request.method in SAFE_METHODS and not request.is_navigation_request():
                return await route.continue_()
            return await route.abort()
        if owner is None:  # a page we didn't open (a popup, a target=_blank tab): nothing it sends gets out
            return await route.abort()
        await owner._judge(route, request)

    async def _judge(self, route: Route, request: Request) -> None:
        active = bool(self._windows)
        window = self._windows[-1] if active else None if self.fresh else self._last_window
        safe = request.method in SAFE_METHODS
        if window is None or self._restoring and safe:
            return await route.continue_()
        acts = request.resource_type in ACTING_TYPES and destroys(urlparse(request.url).path)
        allowed = safe and not acts or _write_allowed(request.url, window["writes"])
        detail, agent = f"{request.method} {request.url}", window["agent"] and active
        if request.is_navigation_request() and request.frame == self._page.main_frame:
            offsite = urlparse(request.url).netloc not in ("", window["host"]) and not self.allow_offsite
            if allowed and window["nav"] and not offsite:
                return await route.continue_()
            window["blocked"].append(f"{detail} (navigation)")
            self._trip("blocked_navigation", detail, agent_action=agent, tool=window["tool"], offsite=allowed and offsite)
            await route.abort("aborted")  # the page stays as it is; a plain abort would show Chromium's error page
        elif not allowed:
            window["blocked"].append(detail)
            self._trip("blocked_write", detail, agent_action=agent, tool=window["tool"])
            await route.abort()
        else:
            await route.continue_()

    def _websocket(self, ws: WebSocketRoute) -> None:
        """Sockets connect and the server's messages arrive, but nothing the page sends gets out once it is not fresh."""
        server = ws.connect_to_server()

        def send(message) -> None:
            if self.fresh and not self._windows:
                server.send(message)
            else:
                self._trip("blocked_write", f"WebSocket send to {ws.url}", agent_action=False, tool=None)

        ws.on_message(send)

    async def close(self) -> None:
        if self._root is not None:  # a fork: close its own forks, then its page, and forget it
            for key in [k for k in self._root._forks if k.startswith(f"{self.owner} > ")]:
                await self._root._forks[key].close()
            if self._page is not None:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = None
            self._root._forks.pop(self.owner, None)
            return
        for fork in list(self._forks.values()):
            await fork.close()
        if self.nvda is not None:
            try:
                await self.nvda.stop()
            except Exception:
                pass
            self.nvda = None
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None


# What a fork keeps for itself; every other attribute lives on the root and is read and written there.
_OWN = frozenset({"current_url", "lock", "dirty", "owner", "_page", "_restoring", "_root", "steps", "steps_origin",
                  "_windows", "_last_window", "fresh"})
_SHARED = frozenset(f.name for f in fields(AuditContext)) - _OWN


def _write_allowed(url: str, prefixes: tuple[str, ...]) -> bool:
    """A --allow-submit prefix covers a URL on the same origin whose path extends the prefix's at a "/" boundary."""
    target = urlparse(url)
    for prefix in map(urlparse, prefixes):
        path = prefix.path.rstrip("/")
        if (target.scheme, target.netloc) == (prefix.scheme, prefix.netloc) and (
                target.path == path or target.path.startswith(path + "/")):
            return True
    return False


class _ForkedContext(AuditContext):
    """A fork made by AuditContext.fork(): never constructed directly (the dataclass __init__ would reset the root)."""

    def __getattribute__(self, name: str):
        if name in _SHARED:
            return getattr(object.__getattribute__(self, "_root"), name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value) -> None:
        if name in _SHARED:
            setattr(object.__getattribute__(self, "_root"), name, value)
        else:
            object.__setattr__(self, name, value)


# ---------------------------------------------------------------------- parallel work on forks

def _keys_for(jobs: dict, page_key) -> dict[str, str | None]:
    if isinstance(page_key, dict):
        return {name: page_key.get(name) for name in jobs}
    return {name: page_key for name in jobs}


async def run_parallel(
    audit: AuditContext,
    jobs: dict[str, Callable[[AuditContext], Awaitable[T]]],
    *,
    page_key: str | dict[str, str | None] | None = None,
) -> dict[str, T | Exception]:
    """Run `jobs` ({name: async fn(context)}) at the same time, each on its own fork of `audit` with `page_key`
    loaded (default: the root's current page; a dict gives each job its own key, None meaning the job loads a page
    itself). Errors are isolated: a failed job returns its exception, the others' results stand. The fork pages
    are closed afterwards; the root's page and cursor are untouched. Everything a job records lands in the shared
    findings, tool runs and rule details.

    Falls back to running the jobs one after another on `audit` itself (restoring each page first) when forks can't
    be used: real NVDA in use (one foreground window), a process state that can't be reloaded, or a single job.
    """
    keys = _keys_for(jobs, audit.current_url if page_key is None else page_key)
    restorable = all(k is None or audit.can_restore(k) for k in keys.values())
    if len(jobs) < 2 or not audit.can_fork() or not restorable:
        results: dict[str, T | Exception] = {}
        for name, job in jobs.items():
            key = keys[name]
            try:
                if key is not None and (audit.current_url != key or key in audit.dirty) and not await audit.restore_page(key):
                    raise RuntimeError(f"{key} could not be restored for {name}")
                results[name] = await job(audit)
            except Exception as exc:
                results[name] = exc
        return results

    async def on_fork(name: str, job) -> T:
        fork = audit.fork(name)
        try:
            key = keys[name]
            if key is not None and not await fork.restore_page(key):
                raise RuntimeError(f"{key} could not be restored for {name}")
            return await job(fork)
        finally:
            await fork.close()

    gathered = await asyncio.gather(*(on_fork(name, job) for name, job in jobs.items()), return_exceptions=True)
    return dict(zip(jobs, gathered))


async def run_agents(
    audit: AuditContext,
    runs: list[tuple["Agent", str]],
    *,
    max_turns: int = 80,
    page_key: str | None = None,
    run_config: Callable[[AuditContext, str], "RunConfig"] | None = None,
) -> list["RunResult | Exception"]:
    """Run several SDK agents on one page at once, each on its own fork (named after the agent), and return their
    RunResults in input order (an Exception where that run failed). `run_config(context, workflow_name)` builds
    each run's RunConfig (pass agent_access.team.runtime.run_config). Same fallbacks as run_parallel; jobs on
    different pages use run_parallel directly."""
    from agents import Runner  # imported here: context.py is imported by the modules that import the SDK runner

    def job_for(agent, prompt, name):
        async def job(context: AuditContext):
            kwargs = {"run_config": run_config(context, f"WCAG audit: {name}")} if run_config else {}
            return await Runner.run(agent, prompt, context=context, max_turns=max_turns, **kwargs)
        return job

    jobs: dict[str, Callable] = {}
    for agent, prompt in runs:
        name, n = agent.name, 1
        while name in jobs:
            n += 1
            name = f"{agent.name} {n}"
        jobs[name] = job_for(agent, prompt, name)
    results = await run_parallel(audit, jobs, page_key=page_key)
    return [results[name] for name in jobs]
