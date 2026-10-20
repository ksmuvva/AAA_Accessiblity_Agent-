"""Command-line entry point for the WCAG 2.2 audit agent team.

Examples:
    python main.py https://example.com
    python main.py --html-file page.html
    python main.py https://example.com --pages 5
    python main.py https://shop.example/cart --allow-interaction --process "go from cart to the payment step"
    python main.py https://example.com --reviewer-model gpt-5 --trace
    python main.py https://example.com --level AAA --screen-reader emulated
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import random
import re
import signal
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from agent_access.budget import Budget
from agent_access.team import run_audit
from agent_access.team.orchestrator import TURNS_PER_PAGE, AuditOutcome, coverage_warnings
from agent_access.report_writer import review_line, write_reports
from agent_access.context import AuditContext
from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.agent_tools.report_tools import compile_site_report
from agent_access.probes.screen_reader import nvda_available


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a web page against WCAG 2.2 (all 86 success criteria).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("url", nargs="?", help="http(s) URL of the page to audit")
    source.add_argument("--html-file", type=Path, help="local HTML file to audit instead of a URL")
    parser.add_argument("--level", choices=["A", "AA", "AAA"], default="AA",
                        help="conformance target (default AA); criteria above it are tested in eager measurement, "
                             "and in lazy measurement only when an agent asks")
    parser.add_argument("--screen-reader", choices=["auto", "nvda", "emulated"], default="auto",
                        help="auto: real NVDA when installed (opens a visible browser; NVDA speaks aloud); "
                             "nvda: require NVDA; emulated: Chromium accessibility tree only")
    parser.add_argument("--pages", type=int, default=1, help="audit up to this many same-site pages (default 1)")
    parser.add_argument("--sample", choices=["wcag-em"],
                        help="choose the pages as WCAG-EM does: one per URL template among the start page's links and "
                             "/sitemap.xml, plus a random 10%%, up to --max-pages; the report states the sample")
    parser.add_argument("--seed", type=int, help="seed of --sample's random pages (default: a new one, in the report)")
    parser.add_argument("--process", help="describe a multi-step process to walk through")
    parser.add_argument("--sr-agent", action="store_true",
                        help="also run the screen reader user on every page: an agent that hears only the screen reader "
                             "and uses only keys, and records where a blind user gets stuck (more model calls per page)")
    parser.add_argument("--uk-statement", action="store_true",
                        help="check the site's accessibility statement against the UK public sector regulations "
                             "(automatic on UK public sector domains such as .gov.uk and .nhs.uk)")
    parser.add_argument("--no-interaction", action="store_true",
                        help="forbid the agents' own clicking, typing and key presses; by default they explore the site "
                             "(requests that change data stay blocked unless --allow-submit). The automated checks "
                             "still press keys, open menus and submit forms, with writes blocked and destructive "
                             "controls skipped")
    parser.add_argument("--allow-interaction", action="store_true", help=argparse.SUPPRESS)  # the old opt-in; now the default
    parser.add_argument("--allow-offsite", action="store_true",
                        help="let free-form interaction follow links to other sites (default: same site only)")
    parser.add_argument("--allow-submit", action="append", metavar="URL_PREFIX",
                        help="let requests that change data (POST etc.) through to URLs starting with this prefix, "
                             "e.g. a test shop's checkout API; repeatable")
    parser.add_argument("--mode", choices=["agent", "code"], default="agent",
                        help="agent (default): the planner agent decides pages, order, flows, review and summary within "
                             "the budget; code: the fixed per-page flow")
    parser.add_argument("--budget-calls", type=int, default=Budget.max_model_calls,
                        help=f"model calls the whole run may spend in agent mode (default {Budget.max_model_calls}; "
                             f"{Budget.reserve_calls} of them are kept for the reviewer and summary)")
    parser.add_argument("--budget-minutes", type=float, default=Budget.max_minutes,
                        help=f"wall-clock minutes after which no new agent work starts (default {Budget.max_minutes:g})")
    parser.add_argument("--max-pages", type=int, default=Budget.max_pages,
                        help=f"ceiling on pages the planner may open in agent mode; --pages is the minimum (default {Budget.max_pages})")
    parser.add_argument("--memory", default=None, metavar="PATH|off",
                        help="SQLite file where runs remember a site and learn from reviewer verdicts "
                             "(default: reports/memory.sqlite; 'off' disables memory)")
    parser.add_argument("--delegation", choices=["handoffs", "tools"], default="handoffs",
                        help="how the lead reaches specialists in agent mode: SDK handoffs (default) or tool calls")
    parser.add_argument("--measurement", choices=["eager", "lazy"], default="lazy",
                        help="lazy (default): the agents choose which checks run, and code runs the remaining checks at "
                             "the target level before a page is declared done (criteria above the target run only if an "
                             "agent asks); eager: every automated check runs when a page loads")
    parser.add_argument("--skip-review", action="store_true", help="don't run the reviewer on failures (cheaper, less reliable)")
    parser.add_argument("--model", help="OpenAI model name (default: $OPENAI_MODEL or the SDK default)")
    parser.add_argument("--reviewer-model",
                        help="model for the reviewer (default: $OPENAI_REVIEWER_MODEL; else the SDK's default model "
                             "when --model names another one; else --model); a different model checks the auditors' "
                             "results more independently")
    parser.add_argument("--trace", action="store_true",
                        help="send agent traces (which include page content and screenshots) to OpenAI")
    parser.add_argument("--no-trace", action="store_true", help=argparse.SUPPRESS)  # the default now; kept for old scripts
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"), help="where to write reports")
    parser.add_argument("--save-login", type=Path, metavar="FILE",
                        help="open a visible browser at the URL, wait while you sign in, save the session to FILE (keep "
                             "it private: it signs in as you) and exit")
    parser.add_argument("--storage-state", type=Path, metavar="FILE",
                        help="audit with a session saved by --save-login, for pages behind a sign-in")
    parser.add_argument("--max-turns", type=int,
                        help=f"safety cap on each lead-auditor run; each page gets its own (default {TURNS_PER_PAGE} in "
                             "code mode, 120 in agent mode)")
    args = parser.parse_args(argv)
    for flag, value in (("--process", args.process), ("--allow-submit", args.allow_submit), ("--allow-offsite", args.allow_offsite)):
        if value and args.no_interaction:
            parser.error(f"{flag} needs interaction; drop --no-interaction")
    if args.budget_calls <= Budget.reserve_calls or args.budget_minutes <= 0 or args.max_pages < 1:
        parser.error(f"--budget-calls must exceed {Budget.reserve_calls}, --budget-minutes must be positive, "
                     "--max-pages must be at least 1")
    args.max_pages = max(args.max_pages, args.pages)
    if args.max_turns is not None and args.max_turns < 1:
        parser.error("--max-turns must be at least 1")
    if any(not prefix.startswith(("http://", "https://")) for prefix in args.allow_submit or ()):
        parser.error("--allow-submit takes absolute URLs, e.g. https://test.shop.example/api/checkout")
    if args.html_file and args.pages > 1:
        parser.error("--pages only works with a URL")
    if args.save_login and args.html_file:
        parser.error("--save-login needs the sign-in page's URL")
    if args.storage_state and not args.storage_state.is_file():
        parser.error(f"--storage-state: {args.storage_state} not found; create it with --save-login")
    if args.pages < 1:
        parser.error("--pages must be at least 1")
    if args.html_file and args.sample:
        parser.error("--sample only works with a URL")
    if args.seed is not None and not args.sample:
        parser.error("--seed only works with --sample")
    if args.sample and args.seed is None:
        args.seed = random.randrange(1_000_000)
    if args.screen_reader == "nvda" and not nvda_available():
        parser.error("NVDA isn't set up. Run: cd nvda_bridge && npm install && "
                     "node node_modules/@guidepup/setup/lib/index.js install nvda")
    return args


def use_headed(args: argparse.Namespace) -> bool:
    """Real NVDA reads the foreground window, so it needs a visible browser."""
    return args.headed or args.screen_reader == "nvda" or (args.screen_reader == "auto" and nvda_available())


def run_dir(base: Path, args: argparse.Namespace) -> Path:
    name = args.html_file.stem if args.html_file else urlparse(args.url).netloc
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-") or "audit"
    return base / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}"


async def run(args: argparse.Namespace) -> int:
    out_dir = run_dir(args.output_dir, args)

    def progress(message: str) -> None:
        print(message, file=sys.stderr)

    headed = use_headed(args)
    if headed and not args.headed:
        progress("Using real NVDA: a browser window will open and NVDA will speak. "
                 "Use --screen-reader emulated to run headless and silent.")
    if not args.no_interaction:
        progress("The agents may click, type and follow links on the site (requests that change data are blocked"
                 + (" except to " + ", ".join(args.allow_submit) if args.allow_submit else "")
                 + "). Use --no-interaction to forbid that.")
    async with AuditContext(headless=not headed, allow_interaction=not args.no_interaction, output_dir=out_dir,
                            target_level=args.level, screen_reader=args.screen_reader,
                            allowed_writes=tuple(args.allow_submit or ()), tracing=args.trace,
                            storage_state=args.storage_state, uk_statement=args.uk_statement) as audit:
        configure_agentic(audit, args, progress)
        preloaded = None
        if args.html_file:
            loaded = await load_html_content(audit, args.html_file.read_text(encoding="utf-8"), f"file:{args.html_file.name}")
            if "error" in loaded:
                print(loaded["error"], file=sys.stderr)
                return 2
            preloaded = loaded["page_label"]

        progress(f"Auditing {preloaded or args.url} ...")
        stopped = None
        task = asyncio.current_task()
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows has no loop signal handlers
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)  # a runner's timeout
        try:
            # A sample's size is capped by --max-pages; in agent mode that is the budget's page cap.
            pages = args.max_pages if args.sample and args.mode == "code" else args.pages
            outcome = await run_audit(audit, start_url=None if preloaded else args.url, pages=pages,
                                      process=args.process, model=args.model, reviewer_model=args.reviewer_model,
                                      max_turns=args.max_turns, review=not args.skip_review, progress=progress,
                                      mode=args.mode, sample_seed=args.seed if args.sample else None)
        except (Exception, asyncio.CancelledError) as exc:  # Ctrl+C, a timeout, a crash: keep what was found
            stopped = f"{type(exc).__name__}: {exc}"
            outcome = AuditOutcome(report=compile_site_report(audit), tool_runs=audit.tool_runs, engines=audit.engines,
                                   warnings=coverage_warnings(audit), tripwires=audit.tripwires, scripts=audit.scripts,
                                   errors=[f"The audit stopped early ({stopped}); this report is incomplete."])

    if outcome.report is None:
        print("No page was audited, so there is no report.", file=sys.stderr)
        for e in outcome.errors:
            print(e, file=sys.stderr)
        return 1

    json_path, md_path, acr_path = write_reports(out_dir, outcome)
    c = outcome.report.counts
    print(
        f"\nFail: {c['fail']}  Needs review: {c['needs_review']}  Not tested: {c['not_tested']}  "
        f"Pass: {c['pass']}  Not applicable: {c['not_applicable']}  "
        f"(of {len(outcome.report.criteria)} criteria at Level {args.level})"
    )
    if outcome.report.beyond_target:
        b = outcome.report.beyond_target_counts
        print(f"Beyond the Level {args.level} target ({len(outcome.report.beyond_target)} criteria): "
              f"{b['fail']} fail, {b['needs_review']} needs review, {b['pass']} pass, {b['not_applicable']} not applicable")
    if outcome.review_log:
        print(f"Reviewer: {review_line(outcome.review_log)}")
    if outcome.budget_used:
        b = outcome.budget_used
        print(f"Budget: {b['model_calls']} of {b['max_model_calls']} model calls, {b['elapsed_minutes']} min; "
              f"{len(outcome.plan_log)} planner decisions; {len(outcome.tripwires)} tripwire(s)")
    if outcome.summary:
        print(f"\n{outcome.summary.overall_assessment}")
    for w in outcome.warnings:
        print(f"Coverage: {w}", file=sys.stderr)
    for e in outcome.errors:
        print(f"Warning: {e}", file=sys.stderr)
    print(f"\nReport: {md_path}\nJSON:   {json_path}\nACR:    {acr_path} (a VPAT-style draft to complete)")
    return 4 if stopped else 0 if not outcome.errors else 1   # 4: incomplete, 1: a stage failed, 2: usage


def configure_agentic(audit: AuditContext, args: argparse.Namespace, progress) -> None:
    """Fields the agentic lanes read from the context: budget (G1), memory (G4), delegation (G2), measurement (G6),
    offsite navigation (G5). Set here, from the flags, so no other module reads argparse."""
    audit.delegation = args.delegation if args.mode == "agent" else "tools"
    audit.measurement = args.measurement
    audit.allow_offsite = args.allow_offsite
    audit.sr_agent = set() if args.sr_agent else None
    if args.mode == "agent":
        audit.budget = Budget(max_model_calls=args.budget_calls, max_minutes=args.budget_minutes, max_pages=args.max_pages)
    if (args.memory or "").lower() != "off":
        try:
            from agent_access.memory import DEFAULT_PATH, Memory
            audit.memory = Memory.open(Path(args.memory) if args.memory else DEFAULT_PATH)
        except ImportError:
            pass
        except Exception as exc:  # a locked or corrupt file: run without memory rather than not at all
            progress(f"Memory disabled: {type(exc).__name__}: {exc}")


async def save_login(url: str, path: Path) -> int:
    """Open a visible browser at `url`; once you have signed in there, Enter here saves the session to `path`."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        await (await context.new_page()).goto(url)
        await asyncio.to_thread(input, f"Sign in in the browser window, then press Enter here to save the session to {path} ")
        await context.storage_state(path=str(path), indexed_db=True)  # some sign-ins (Firebase) keep their token there
        await browser.close()
    print(f"Saved. Audit with: --storage-state {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which can't print summaries quoting non-Latin page text.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    args = parse_args(argv)
    if args.save_login:
        return asyncio.run(save_login(args.url, args.save_login))
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Add it to .env or your environment.", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
