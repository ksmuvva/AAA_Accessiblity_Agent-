"""Tuning the 3.x and 4.x rules: defects they used to miss, and situations where they recorded nothing."""

from audit_helpers import judge, statuses
from agent_access.models import Status


def test_form_defects_fixture_fails_the_rules_built_for_it():
    audit = judge("u_forms_bad.html", "3.2.2", "3.3.1", "3.3.4", "3.3.8", "3.3.9", "4.1.3")
    # A select whose onchange navigates is a change of context on input.
    assert Status.FAIL in statuses(audit, "3.2.2")
    # The error text appears in a plain div while focus stays on the submit button: not announced.
    assert Status.FAIL in statuses(audit, "4.1.3")
    assert Status.PASS in statuses(audit, "3.3.1")  # ... but the error was identified in text
    # Password field blocks pasting; a CAPTCHA has no alternative.
    assert Status.FAIL in statuses(audit, "3.3.8") and Status.FAIL in statuses(audit, "3.3.9")
    # Card number field: a financial form needs review/confirm/undo.
    assert statuses(audit, "3.3.4") == {Status.NEEDS_REVIEW}


def test_unclickable_submit_controls_do_not_break_the_forms_probe():
    """A hidden submit button, and one covered by an overlay (W3C BAD survey page), used to raise out of the
    probe and turn every forms rule into an error. Now the form is submitted with Enter and judged."""
    from audit_helpers import data

    html = ('<html lang="en"><body>'
            '<form id="hidden" novalidate><label for="a">Name</label><input id="a" required>'
            '<button type="submit" style="display:none">Send</button></form>'
            '<form id="covered" novalidate><label for="b">Email</label><input id="b" type="email" required>'
            '<button type="submit" id="cov">Send</button>'
            '<div style="position:fixed;inset:0;background:rgba(0,0,0,.01)"></div></form>'
            '<script>for (const f of document.forms) f.addEventListener("submit", (e) => { e.preventDefault();'
            'const p = document.createElement("p"); p.textContent = "Please fill in the field."; f.append(p); });'
            '</script></body></html>')
    audit = judge(html, "3.3.1")
    forms = data(audit, "forms")
    assert all("error" not in s for s in forms["submissions"]), forms["submissions"]
    assert {s["submit"] for s in forms["submissions"]} == {"pressed Enter in the first field"}
    assert Status.PASS in statuses(audit, "3.3.1") and Status.FAIL not in statuses(audit, "3.3.1")


def test_non_english_pages_get_review_not_a_reading_grade_or_false_not_applicable():
    japanese = ('<html lang="ja"><head><title>テスト</title></head><body><main><h1>見出し</h1><p>'
                + "これは日本語の長い段落です。単語の区切りに空白がないため、英語向けの可読性の式は使えません。" * 6
                + '</p></main></body></html>')
    assert statuses(judge(japanese, "3.1.5"), "3.1.5") == {Status.NEEDS_REVIEW}


def test_short_foreign_script_text_is_a_name_not_an_unmarked_passage():
    arabic = ('<html lang="ar" dir="rtl"><head><title>أخبار</title></head><body><main><h1>بي بي سي</h1>'
              '<p>هذه فقرة طويلة بما يكفي باللغة العربية لتكون نصاً حقيقياً في الصفحة.</p>'
              '<p>BBC News Arabic Service</p>'  # 20+ characters, so the page probe lists it as a passage
              '<p>This is a full English sentence inside an Arabic page that should carry a lang attribute.</p>'
              '</main></body></html>')
    audit = judge(arabic, "3.1.2")
    fails = [f for f in audit.findings if f.wcag_criterion == "3.1.2" and f.status is Status.FAIL]
    assert len(fails) == 1 and len(fails[0].selectors) == 1  # only the English sentence, not "BBC News Arabic"
    assert any("brand or product name" in f.description for f in audit.findings if f.wcag_criterion == "3.1.2")


def test_announced_errors_pass_status_messages():
    html = ('<html lang="en"><body><form id="f" novalidate><label for="e">Email</label><input id="e" type="email" required>'
            '<button type="submit">Send</button><div role="alert" id="msg"></div></form>'
            '<script>document.getElementById("f").addEventListener("submit", (ev) => { ev.preventDefault();'
            'document.getElementById("msg").textContent = "Enter your email address."; });</script></body></html>')
    audit = judge(html, "4.1.3")
    assert Status.FAIL not in statuses(audit, "4.1.3") and Status.PASS in statuses(audit, "4.1.3")


def test_text_only_form_records_a_verdict_for_on_input():
    html = ('<html lang="en"><body><form><label for="q">Search</label><input id="q" type="search">'
            '<button type="submit">Go</button></form></body></html>')
    assert statuses(judge(html, "3.2.2"), "3.2.2") == {Status.PASS}
    with_handler = html.replace('type="search"', 'type="search" onchange="go()"')
    assert statuses(judge(with_handler, "3.2.2"), "3.2.2") == {Status.NEEDS_REVIEW}


def test_consistent_order_rules_ask_for_a_look_when_nothing_was_identified():
    import asyncio

    from agent_access.agent_tools.browser_tools import load_html_content
    from agent_access.context import AuditContext
    from agent_access.engine import run_rule
    from agent_access.rules import REGISTRY

    pages = {"a": '<html lang="en"><body><p>Page A, no navigation or help.</p></body></html>',
             "b": '<html lang="en"><body><nav><a href="/x">X</a></nav><a href="/help">Help</a></body></html>'}

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            # Each page's navigation is captured when a rule runs on it; the comparison happens on the last page.
            for label, html in pages.items():
                await load_html_content(audit, html, label)
                for criterion in ("3.2.3", "3.2.6"):
                    await run_rule(audit, REGISTRY[criterion])
            return audit

    audit = asyncio.run(scenario())
    on_b = {f.wcag_criterion: (f.status, f.description) for f in audit.findings if f.page_url == "b"}
    assert {c: s for c, (s, _) in on_b.items()} == {"3.2.3": Status.NEEDS_REVIEW, "3.2.6": Status.NEEDS_REVIEW}
    assert all("identified on a" in d for _, d in on_b.values())  # not the generic "no evidence" placeholder


def test_a_second_link_to_the_same_page_is_not_an_inconsistent_name():
    import asyncio

    from agent_access.agent_tools.browser_tools import load_html_content
    from agent_access.context import AuditContext
    from agent_access.engine import run_rule
    from agent_access.rules import REGISTRY

    menu = '<nav><a href="/home.html">Home</a><a href="/news.html">News</a></nav>'
    pages = {"home": f'<html lang="en"><body>{menu}<a href="/home.html">Inaccessible Home Page</a></body></html>',
             "news": f'<html lang="en"><body>{menu}<p>News</p></body></html>',
             "about": f'<html lang="en"><body><nav><a href="/home.html">Start</a><a href="/news.html">News</a></nav></body></html>'}

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            for label, html in pages.items():
                await load_html_content(audit, html, label)
                await run_rule(audit, REGISTRY["3.2.4"])
            return audit

    audit = asyncio.run(scenario())
    renamed = [f for f in audit.findings if f.wcag_criterion == "3.2.4" and "different names" in f.description]
    # Only "about" names /home.html with a name no other page uses ("Start"); home's extra link isn't a conflict.
    assert [f.page_url for f in renamed] == ["about"] and "/home.html" in renamed[0].evidence


def test_a_date_field_with_no_stated_format_goes_to_the_agent():
    """The eval's booking_1 false pass: "Date" with no format passed 3.3.2. A label, placeholder or hint giving the
    format (or a native date picker) is enough."""
    from audit_helpers import found

    def flagged(field):
        html = (f'<!doctype html><html lang="en"><head><title>Book</title></head><body><main><h1>Book</h1>'
                f'<form action="/b">{field}<button>Book</button></form></main></body></html>')
        return [f for f in found(judge(html, "3.3.2"), "3.3.2").get(Status.NEEDS_REVIEW, []) if "date field" in f.description]
    assert flagged('<label for="d">Date</label><input id="d" name="day" type="text">')
    assert flagged('<label for="b">Date of birth</label><input id="b" type="text">')
    assert not flagged('<label for="d">Date (DD/MM/YYYY)</label><input id="d" type="text">')
    assert not flagged('<label for="d">Date</label><input id="d" type="text" aria-describedby="h"><p id="h">For example, 27 3 2026</p>')
    assert not flagged('<label for="d">Date</label><input id="d" type="text" placeholder="05/11/2026">')
    assert not flagged('<label for="d">Date</label><input id="d" type="date">')


def test_a_passage_in_another_latin_language_is_measured():
    """French in an English page, with no lang of its own, used to be left to the agents (same script). Language
    identification fails it when the passage is long enough and the detector is sure; English text and a short French
    phrase stay with the judgement."""
    from audit_helpers import found

    def fails(body, lang="en", criterion="3.1.2"):
        html = f'<!doctype html><html lang="{lang}"><head><title>Shop</title></head><body><main><h1>Shop</h1>{body}</main></body></html>'
        return [f for f in found(judge(html, criterion), criterion).get(Status.FAIL, []) if "identification" in f.description]
    french = "<p>Bonjour à tous, nous sommes ravis de vous accueillir dans notre nouvelle boutique en ligne.</p>"
    english = "<p>Our annual report for 2025 is now available, with results for every store and every region.</p>"
    assert fails(english + french) and not fails(english) and not fails(english + "<p>Bonjour à tous !</p>")
    assert not fails(english + french.replace("<p>", '<p lang="fr">'))
    assert fails(french * 3, criterion="3.1.1") and not fails(english * 3, criterion="3.1.1")
