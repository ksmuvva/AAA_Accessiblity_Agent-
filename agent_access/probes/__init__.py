"""Probes: the collection layer. Each probe measures the page and returns raw data, never verdicts.

    @probe("keyboard")
    async def keyboard(audit) -> dict: ...

`collect(audit, name)` runs a probe at most once per page and caches the result in audit.observations, so the
86 rules (agent_access/rules) share one measurement. Interactive probes restore the page when they're done. Probes
that accept `targets` can be re-run on specific elements; `merge` folds that data into the cached result.
Judgement belongs to the rules; probes only report what they saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from agent_access.context import AuditContext


@dataclass
class ProbeResult:
    data: dict
    error: str | None = None


@dataclass(frozen=True)
class Probe:
    name: str
    run: Callable[..., Awaitable[dict]]
    retestable: bool  # accepts targets=[selectors] to measure specific elements again


PROBES: dict[str, Probe] = {}


def probe(name: str, *, retestable: bool = False):
    def register(fn):
        PROBES[name] = Probe(name, fn, retestable)
        return fn
    return register


async def collect(audit: AuditContext, name: str) -> ProbeResult:
    """Run probe `name` on the current page once; later calls return the cached result.

    Forks of one AuditContext (parallel agents) share the cache and a lock per (page, probe), so concurrent calls
    for the same page run the probe once, on whichever fork got there first, and all get its result.
    """
    page_key = audit.current_url
    cache = audit.observations.setdefault(page_key, {})
    if name in cache:
        return cache[name]
    async with audit.probe_lock(page_key, name):
        if name not in cache:  # another fork may have measured it while we waited
            with audit.at_page(page_key):  # interactive probes may restore the page and move the cursor
                try:
                    data, error = await PROBES[name].run(audit), None
                except Exception as exc:  # A broken probe must not stop the others; rules report the error.
                    data, error = {}, f"{type(exc).__name__}: {str(exc)[:200]}"
            cache[name] = ProbeResult(data, error or data.get("error"))
    return cache[name]


# Sweep order: read-only measurements first, then probes that resize the viewport or move focus, then probes
# that interact with the page (and restore it). The screen reader reuses the keyboard probe's Tab stops.
PROBE_ORDER = [
    "axe", "page", "instrumentation", "nav", "media", "motion", "contrast",
    "text_spacing", "reflow", "targets", "keyboard", "zoom", "orientation", "flashing", "screen_reader",
    "skip_link", "sliders", "hover", "disclosures", "shortcuts", "pointer", "forms", "timeouts",
]


def _load() -> None:
    # Importing the modules runs their @probe decorators.
    from agent_access.probes import axe, contrast, forms, interaction, keyboard, layout, page, screen_reader  # noqa: F401

    assert set(PROBES) == set(PROBE_ORDER), set(PROBES) ^ set(PROBE_ORDER)


_load()
