"""WCAG-EM sampling (agent_access/sampling.py): one page per URL template plus a seeded random tenth, in both modes."""

import asyncio
import functools
import http.server
import threading

from test_agent import SUMMARY, RouterModel, confirm_all, first_user_text

from agent_access.agent_tools.browser_tools import open_url
from agent_access.budget import Budget
from agent_access.context import AuditContext
from agent_access.report_writer import render_markdown
from agent_access.sampling import template, wcag_em_sample
from agent_access.team.orchestrator import run_audit
from agent_access.team.planner import run_agentic_audit

LINKED = ["about.html", "contact.html", "products/1.html", "products/2.html", "products/3.html", "blog/a-post.html",
          "blog/b-post.html", "faq.html", "terms.html", "news.html", "jobs.html", "team.html"]
LISTED = ["help.html", "products/4.html", "report.pdf"]


def serve(tmp_path, children=()):
    """The index links to LINKED. /sitemap.xml redirects (it is a directory) to a sitemap index whose sitemap lists
    LISTED and an off-site page; `children` are more sitemaps the index lists."""
    tmp_path.mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    for name in ["index.html"] + LINKED + LISTED[:2]:
        links = "".join(f'<a href="/{n}">{n}</a> ' for n in LINKED) if name == "index.html" else ""
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(f"<html lang='en'><head><title>{name}</title></head><body><main><h1>{name}</h1>"
                                     f"<p>{links}</p></main></body></html>", encoding="utf-8")
    locs = "".join(f"<url><loc>{base}/{n}</loc></url>" for n in LISTED) + "<url><loc>https://elsewhere.example/x</loc></url>"
    (tmp_path / "sitemap.xml").mkdir()
    (tmp_path / "sitemap.xml" / "index.html").write_text("<sitemapindex>" + "".join(
        f"<sitemap><loc>{c}</loc></sitemap>" for c in [*children, f"{base}/pages.xml"]) + "</sitemapindex>")
    (tmp_path / "pages.xml").write_text(f"<urlset>{locs}</urlset>")
    return base, server


def urls(base, *names):
    return [f"{base}/{n}" for n in names]


def test_urls_that_differ_in_an_id_or_a_slug_share_a_template():
    assert template("https://s.example/products/12") == template("https://s.example/products/7") == "/products/{id}"
    assert template("https://s.example/blog/a-post") == template("https://s.example/blog/another/") == "/blog/{slug}"
    assert template("https://s.example/about") != template("https://s.example/contact")
    assert template("https://s.example/item.php?id=3") == "/item.php?id"


def test_the_sample_takes_a_page_per_template_and_a_seeded_random_tenth(tmp_path):
    base, server = serve(tmp_path)

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await open_url(audit, f"{base}/index.html")
            return [await wcag_em_sample(audit, cap, seed=3) for cap in (20, 20, 10, 3)]

    try:
        full, again, capped, small = asyncio.run(scenario())
    finally:
        server.shutdown()
    # One page per template from the links, then help from the sitemap (reached through a redirect). Not the PDF or
    # the off-site page.
    every = urls(base, "index.html", "about.html", "contact.html", "products/1.html", "blog/a-post.html", "faq.html",
                 "terms.html", "news.html", "jobs.html", "team.html", "help.html")
    assert full["structured"] == every and len(full["random"]) == 2   # 11 templates: a random tenth is 2 pages
    assert set(full["random"]) <= set(urls(base, "products/2.html", "products/3.html", "blog/b-post.html", "products/4.html"))
    assert again == full and full["from_sitemap"] == 4 and full["candidates"] == 14   # the same seed, the same sample
    # Capped, the structured part shrinks until it and its own random tenth fit: 9 + 1 of 10, 2 + 1 of 3.
    assert capped["structured"] == every[:9] and len(capped["random"]) == 1
    assert small["pages"] == every[:2] + small["random"] and len(small["random"]) == 1


def test_a_sitemap_cannot_send_the_auditor_to_another_site(tmp_path):
    (tmp_path / "other").mkdir()
    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "other")))
    threading.Thread(target=other.serve_forever, daemon=True).start()
    other_base = f"http://127.0.0.1:{other.server_address[1]}"   # another port: another site, though reachable
    base, server = serve(tmp_path / "site", children=[f"{other_base}/other.xml"])
    (tmp_path / "other" / "other.xml").write_text(f"<urlset><url><loc>{base}/from-other.html</loc></url></urlset>")

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await open_url(audit, f"{base}/index.html")
            return await wcag_em_sample(audit, 20, seed=3)

    try:
        sample = asyncio.run(scenario())
    finally:
        server.shutdown()
        other.shutdown()
    assert sample["from_sitemap"] == 4   # only the site's own sitemap was read


def test_code_mode_audits_the_sample_instead_of_the_picks_and_reports_it(tmp_path):
    base, server = serve(tmp_path)
    model = RouterModel({"page picker": [], "lead auditor": [{"notes_for_human": []}] * 6, "reviewer": [confirm_all] * 6,
                         "summary writer": [SUMMARY]})

    async def scenario():
        async with AuditContext(output_dir=tmp_path / "out", screen_reader="emulated") as audit:
            return await run_audit(audit, start_url=f"{base}/index.html", pages=3, model=model, max_turns=5,
                                   progress=lambda m: None, sample_seed=3)

    try:
        outcome = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert "page picker" not in model.inputs
    assert [p.page_url for p in outcome.report.pages] == outcome.sample["pages"] and len(outcome.sample["pages"]) == 3
    assert "## Sample" in render_markdown(outcome) and "Seed 3" in render_markdown(outcome)
    assert outcome.engines["browser"].startswith("Chromium ")


def test_agent_mode_opens_the_sample_and_the_floor_audits_every_page(tmp_path):
    base, server = serve(tmp_path)
    gave_up = {"pages_audited": [], "goal_changes": [], "stopped_because": "gave up", "notes_for_human": []}
    model = RouterModel({"audit planner": [gave_up] * 3, "lead auditor": [{"notes_for_human": []}] * 6,
                         "reviewer": [confirm_all] * 6, "summary writer": [SUMMARY]})

    async def scenario():
        async with AuditContext(output_dir=tmp_path / "out", screen_reader="emulated", tracing=False) as audit:
            audit.budget = Budget(max_pages=3)
            await open_url(audit, f"{base}/index.html")
            return await run_agentic_audit(audit, model=model, progress=lambda m: None, sample_seed=3)

    try:
        outcome = asyncio.run(scenario())
    finally:
        server.shutdown()
    prompt = first_user_text(model.inputs["audit planner"][0])
    assert prompt.startswith(f"Start page: {base}/index.html.") and "A WCAG-EM sample of 3 pages is open" in prompt
    assert [p.page_url for p in outcome.report.pages] == outcome.sample["pages"]
    # The planner gave up at once: the code floor ran the lead on every sampled page.
    assert [e["page"] for e in outcome.plan_log if e["action"] == "lead_floor"] == outcome.sample["pages"]


def test_sample_flags_need_a_url_and_always_get_a_seed():
    import pytest
    from agent_access.cli import parse_args
    assert parse_args(["https://x.example", "--sample", "wcag-em", "--seed", "5"]).seed == 5
    assert isinstance(parse_args(["https://x.example", "--sample", "wcag-em"]).seed, int)   # a new one, reported
    for wrong in (["https://x.example", "--seed", "5"], ["--html-file", "page.html", "--sample", "wcag-em"]):
        with pytest.raises(SystemExit):
            parse_args(wrong)
