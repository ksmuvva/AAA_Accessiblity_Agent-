"""The UK accessibility statement. The Public Sector Bodies (Websites and Mobile Applications) (No. 2) Accessibility
Regulations 2018 require a UK public sector website to publish one, and GOV.UK's model statement sets out what it must
say. The check runs on a UK public sector host (by its domain) or on any site with --uk-statement, after the report is
compiled. It finds the statement's link on the site's homepage (or the first audited pages) and reads the statement in
a tab of its own (a fork, so the network route lets it load; never an audited page). It looks for each required part by
its usual wording, then compares the compliance the statement claims with this audit's Level A and AA failures.

Wording is matched, not understood: "not found" means the usual wording isn't there, and a reader should confirm it.
The result is its own section of the report, not part of the WCAG criteria.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from agent_access.agent_tools.browser_tools import refused
from agent_access.context import AuditContext, is_state_key
from agent_access.jsassets import js
from agent_access.models import CriterionResult, SiteReport, Status

UK_PUBLIC_SECTOR = (".gov.uk", ".nhs.uk", ".nhs.scot", ".gov.scot", ".gov.wales", ".llyw.cymru", ".police.uk",
                    ".mod.uk", ".parliament.uk", ".ac.uk")
PAGES_SEARCHED = 3
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
_MONTH = "|".join(MONTHS)
DATE_TEXT = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?(?:{_MONTH}),?\s+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}"


def uk_public_sector(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s[1:] or host.endswith(s) for s in UK_PUBLIC_SECTOR)


def _rank(link: dict) -> int | None:
    """How surely a link is the statement's: its text, then its address. Without "statement", only a short text that
    starts with "Accessibility" or an address ending in /accessibility counts, not a headline about accessibility."""
    text, path = link["text"].lower(), urlparse(link["href"]).path.lower()
    for rank, hit in enumerate(("accessibility statement" in text, re.search(r"accessibility[-_]?statement", path),
                                re.fullmatch(r"accessibility\b.{0,20}", text), re.search(r"/accessibility(?:\.\w+)?/?$", path))):
        if hit:
            return rank
    return None


def statement_link(links: list[dict], page_url: str) -> str | None:
    ranked = [(r, i, link["href"]) for i, link in enumerate(links)
              if urlparse(link["href"]).scheme in ("http", "https") and link["href"].split("#")[0] != page_url.split("#")[0]
              and (r := _rank(link)) is not None]
    return min(ranked)[2] if ranked else None


def _parse_date(text: str) -> date | None:
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):   # UK order: day/month/year
        day, month, year = map(int, m.groups())
    else:
        m = re.fullmatch(rf"(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+)?({_MONTH}),?\s+(\d{{4}})", text)
        day, month, year = int(m[1] or 1), MONTHS.index(m[2]) + 1, int(m[3])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_after(text: str, lead: str) -> date | None:
    """The latest date that follows `lead` ("reviewed on 29 January 2026", "Last updated: 29 January 2026")."""
    found = (_parse_date(m[1]) for m in re.finditer(rf"{lead}:?\s+(?:on\s+|in\s+)?(?:the\s+)?({DATE_TEXT})", text))
    return max((d for d in found if d), default=None)


def _mentions(text: str, c: CriterionResult) -> bool:
    return bool(re.search(rf"(?<![\d.]){re.escape(c.wcag_criterion)}(?!\d)", text)) or c.title.lower() in text


def assess(text: str, links: list[dict], report: SiteReport, today: date) -> tuple[str | None, list[dict]]:
    """The compliance the statement claims ("fully", "partially", "not" or None) and one row per required part:
    found True, False, or None where the part doesn't apply."""
    text = " ".join(text.split()).lower()
    rows: list[dict] = []

    def row(requirement: str, found: bool | None, detail: str = "") -> None:
        rows.append({"requirement": requirement, "found": found, "detail": detail})

    claim = m[1] if (m := re.search(r"\b(fully|partially|not) compliant\b", text)) else None
    row("Compliance status (fully, partially or not compliant)", claim is not None,
        f"{claim} compliant" if claim else "none of the three is stated")
    versions = sorted(set(re.findall(r"(?:wcag|web content accessibility guidelines)\s*(?:version\s*)?(2\.[0-2])", text)))
    row("Names WCAG 2.2", "2.2" in versions,
        "names WCAG " + ", ".join(versions) + ("" if "2.2" in versions else "; the model statement uses 2.2")
        if versions else "no WCAG version is named")
    row("Names the 2018 accessibility regulations",
        bool(re.search(r"accessibility regulations 2018|websites and mobile applications\)", text)))
    if claim in ("partially", "not"):
        row("Lists the non-accessible content (non-compliance, disproportionate burden, out of scope)",
            bool(re.search(r"non-accessible content|non-compliance with the accessibility regulations|"
                           r"disproportionate burden|not within the scope", text)))
    else:
        row("Lists the non-accessible content (non-compliance, disproportionate burden, out of scope)", None,
            "not required when fully compliant" if claim else "depends on the compliance status")
    prepared = _date_after(text, "prepared")
    row("Says when it was prepared", prepared is not None, prepared.isoformat() if prepared else "")
    latest = max((d for d in (prepared, _date_after(text, r"(?:reviewed|updated)")) if d), default=None)
    months = (today.year - latest.year) * 12 + today.month - latest.month if latest else None
    row("Reviewed in the last 12 months", latest is not None and months <= 12,
        f"last reviewed or prepared {latest.isoformat()}" if latest else "no review date is given")
    hrefs = [link["href"].lower() for link in links]
    contact = [kind for kind, hit in (
        ("email", re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text) or any(h.startswith("mailto:") for h in hrefs)),
        ("phone", re.search(r"(?:phone|telephone|call)\D{0,30}\d[\d ]{6,}", text) or any(h.startswith("tel:") for h in hrefs)),
        ("form", re.search(r"contact (?:us|form)|feedback form", text)
         or any(re.search(r"contact|feedback", link["text"], re.I) for link in links)),
    ) if hit]
    row("How to give feedback or ask for an accessible format", bool(contact), ", ".join(contact))
    named = re.search(r"equality and human rights commission|\behrc\b|equality commission for northern ireland|\becni\b", text)
    linked = any((urlparse(h).hostname or "").endswith(("equalityadvisoryservice.com", "equalityni.org")) for h in hrefs)
    row("Enforcement procedure: the EHRC, with a link to EASS (the ECNI in Northern Ireland)", bool(named and linked),
        "" if named and linked else "the enforcement body isn't named" if not named else "no link to EASS or the ECNI")
    row("How the website was tested", bool(re.search(r"\b(?:was|were) (?:last )?tested\b|\btest(?:s|ing)?\b[^.]{0,150}"
                                                     r"\bcarried out\b|\bhow we tested\b|\bwe tested\b", text)))
    failing = [c for c in (*report.criteria, *report.beyond_target) if c.status is Status.FAIL and c.level in ("A", "AA")]
    if claim == "fully":
        row("Agrees with this audit's Level A and AA failures", not failing,
            f"claims full compliance, but the audit found {len(failing)} failure(s): "
            + ", ".join(c.wcag_criterion for c in failing) if failing else "")
    elif claim:
        missing = [c for c in failing if not _mentions(text, c)]
        row("Agrees with this audit's Level A and AA failures", not missing,
            "doesn't mention " + ", ".join(f"{c.wcag_criterion} {c.title}" for c in missing) if missing else "")
    else:
        row("Agrees with this audit's Level A and AA failures", None, "no compliance status to compare")
    return claim, rows


async def _read(page, url: str, script: str):
    response = await page.goto(url, wait_until="load", timeout=30_000)
    if response is not None and response.status >= 400:
        raise RuntimeError(f"HTTP {response.status}")
    return await page.evaluate(script)


async def check_statement(audit: AuditContext, report: SiteReport) -> tuple[dict | None, list[str]]:
    """The statement's assessment (None when the check doesn't apply), and a warning when it couldn't be made."""
    starts = [p for p in audit.pages if p.startswith(("http://", "https://")) and not is_state_key(p)]
    if not starts or not (audit.uk_statement or uk_public_sector(urlparse(starts[0]).hostname or "")):
        return None, ["The UK accessibility statement was not checked: no web page was audited."] if audit.uk_statement else []
    first = urlparse(starts[0])
    searched = list(dict.fromkeys([f"{first.scheme}://{first.netloc}/", *starts]))[:PAGES_SEARCHED]
    tab = audit.fork("statement")
    result: dict = {"checked": False}
    try:
        page = await tab.page()
        failed: dict[str, str] = {}
        for url in searched:
            try:
                if found := statement_link(await _read(page, url, js("statement/links")), url):
                    result.update(url=found, linked_from=url)
                    break
            except Exception as exc:   # a page that won't load: try the next
                failed[url] = (str(exc).splitlines() or [type(exc).__name__])[0][:100]
        if "url" not in result:
            read = [u for u in searched if u not in failed]
            result["reason"] = "; ".join(
                ([f"no link to an accessibility statement was found on {', '.join(read)}"] if read else [])
                + [f"{u} could not be loaded ({why})" for u, why in failed.items()])
        elif why := refused(audit, result["url"]):
            result["reason"] = f"its link goes to {result['url']}, which the audit doesn't open ({why})"
        else:
            try:
                text = await _read(page, result["url"], "() => document.body ? document.body.innerText : ''")
                links = await page.evaluate(js("statement/links"))
            except Exception as exc:
                result["reason"] = f"{result['url']} could not be read ({type(exc).__name__}: {str(exc)[:120]})"
            else:
                result["claim"], result["checks"] = assess(text, links, report, date.today())
                result["checked"] = True
    except Exception as exc:   # the browser went away: the rest of the report still stands
        result["reason"] = f"the check failed ({type(exc).__name__})"
    finally:
        await tab.close()
    if not result["checked"]:
        return result, [f"The UK accessibility statement was not checked: {result['reason']}."]
    return result, []
