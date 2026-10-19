"""Principle 3 - Understandable: 3.1.1 to 3.3.9."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urldefrag

from langdetect import DetectorFactory, LangDetectException, detect_langs

from agent_access.probes.page import primary_nav
from agent_access.rules import AXE, PW, SR, Ctx, wcag_tool
from agent_access.rules.common import (
    completed_submissions, form_fields, inspectable_stops, no_stops, pass_walk, submission_facts, untested_submissions,
)

# Writing systems by Unicode range, and the script each language code is written in.
SCRIPTS = {
    "Cyrillic": (0x0400, 0x04FF), "Greek": (0x0370, 0x03FF), "Arabic": (0x0600, 0x06FF), "Hebrew": (0x0590, 0x05FF),
    "Devanagari": (0x0900, 0x097F), "Thai": (0x0E00, 0x0E7F), "Han": (0x4E00, 0x9FFF), "Hangul": (0xAC00, 0xD7AF),
    "Kana": (0x3040, 0x30FF), "Latin": (0x0041, 0x024F),
}
LANG_SCRIPT = {"ru": "Cyrillic", "uk": "Cyrillic", "bg": "Cyrillic", "sr": "Cyrillic", "el": "Greek", "ar": "Arabic",
               "fa": "Arabic", "ur": "Arabic", "he": "Hebrew", "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari",
               "th": "Thai", "zh": "Han", "ja": "Han", "ko": "Hangul"}
ACRONYM = re.compile(r"\b[A-Z]{2,6}s?\b")
COMMON_ACRONYMS = {"OK", "TV", "USA", "UK", "EU", "PDF", "FAQ", "URL", "AM", "PM", "ID", "PIN", "HTML", "CSS", "ASAP"}
HELP_HINT = re.compile(r"\b(example|format|e\.g\.|for instance|must|should|at least|between|like|such as|enter a|use )\b", re.I)
RISKY_FORM = re.compile(r"\b(card|cvv|cvc|payment|pay|purchase|order|checkout|bank|iban|account number|transfer|delete|"
                        r"terms|contract|agree|legal|exam|test|answer|submit application|tax)\b", re.I)
DATE_FIELD = re.compile(r"\b(date|dob|birth\w*|bday|expir\w*|cc-exp\w*)\b", re.I)
FORMAT_HINT = re.compile(r"\b(dd|mm|yy|yyyy|day|month|year|for example|e\.?g)\b|\d{1,2}\s*[/.-]\s*\d{1,2}", re.I)
STEPS = re.compile(r"\bstep\s*\d+\s*(of|/)\s*\d+\b", re.I)
MIN_FOREIGN_PASSAGE = 40  # characters: below this, text in another script is probably a name, not a passage
MIN_DETECT_CHARS = 80     # below this, statistical language identification is unreliable
DetectorFactory.seed = 0  # langdetect samples at random; a fixed seed makes its answer repeatable
# Languages the detector can't reliably tell apart: a detection within one of these groups is no mismatch.
CLOSE = [{"da", "no", "nb", "nn"}, {"hr", "sr", "bs"}, {"id", "ms", "in"}, {"he", "iw"}]


def _language(text: str) -> str | None:
    """The language a passage is written in (e.g. 'fr'), when the detector is at least 95% sure; else None."""
    if len(text.strip()) < MIN_DETECT_CHARS:
        return None
    try:
        best = detect_langs(text)[0]
    except LangDetectException:
        return None
    return best.lang.split("-")[0] if best.prob >= 0.95 else None


def _other_language(text: str, lang: str | None) -> str | None:
    """The detected language of `text` when it confidently differs from `lang` (outside close groups)."""
    declared, found = (lang or "").lower().split("-")[0], _language(text)
    return found if declared and found and found != declared and not any({found, declared} <= g for g in CLOSE) else None


def _script(text: str) -> str | None:
    counts = Counter()
    for ch in text:
        cp = ord(ch)
        for name, (lo, hi) in SCRIPTS.items():
            if lo <= cp <= hi:
                counts["Han" if name == "Kana" else name] += 1
                break
    total = sum(counts.values())
    if total < 8:
        return None
    script, n = counts.most_common(1)[0]
    return script if n / total > 0.6 else None


def _expected_script(lang: str | None) -> str | None:
    if not lang:
        return None
    return LANG_SCRIPT.get(lang.lower().split("-")[0], "Latin")


def _syllables(word: str) -> int:
    word = word.lower().strip(".,;:!?\"'()")
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", word)
    word = re.sub(r"^y", "", word)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", word)))


def flesch_kincaid_grade(text: str) -> tuple[float, int]:
    sentences = max(1, len(re.findall(r"[.!?]+(\s|$)", text)))
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if not words:
        return 0.0, 0
    syllables = sum(_syllables(w) for w in words)
    return round(0.39 * len(words) / sentences + 11.8 * syllables / len(words) - 15.59, 1), len(words)


def _context_changes(forms: dict) -> list[dict]:
    """Controls whose change navigated (or tried to) without the user submitting."""
    return [c for change in forms.get("changes", []) for c in change["changes"] if c["navigation"] or c["url_changed"]]


# ---------------------------------------------------------------- 3.1 Readable

@wcag_tool("3.1.1", "check_page_language", domain="lead", tech=(AXE, PW), uses=("axe", "page"), judgement=True)
def check_page_language(ctx: Ctx) -> None:
    """The page's default language must be set (lang) and match its content."""
    ctx.axe()
    page = ctx.data("page")
    lang, sample = page.get("page_lang"), page.get("lang_sample", page.get("main_text", ""))[:1500]
    detected = _script(sample)
    expected = _expected_script(lang)
    if lang and detected and expected and detected != expected:
        ctx.fail(f"The page is declared lang='{lang}' ({expected} script) but its text is mostly {detected}.")
    elif found := _other_language(sample, lang):
        ctx.fail(f"The page is declared lang='{lang}' but its text is in '{found}' (language identification).",
                 evidence=sample[:200])
    elif lang:
        ctx.review(f"Check lang='{lang}' matches the page's main language.", evidence=sample[:200])


@wcag_tool("3.1.2", "check_language_of_parts", domain="lead", tech=(AXE, PW), uses=("axe", "page"), judgement=True)
def check_language_of_parts(ctx: Ctx) -> None:
    """Passages in another language must be marked with lang; judge Latin-script passages (e.g. French in English) yourself."""
    ctx.axe()
    page = ctx.data("page")
    wrong, short = [], []
    for p in page.get("passages", []):
        detected, expected = _script(p["text"]), _expected_script(p["lang"])
        if detected and expected and detected != expected:
            # A short run of another script is often a brand or product name (BBC, iPhone); a real passage is longer.
            (wrong if len(p["text"].strip()) >= MIN_FOREIGN_PASSAGE else short).append(p)
    if wrong:
        ctx.fail(f"{len(wrong)} passage(s) are written in a different script from their declared language.",
                 [p["selector"] for p in wrong], "; ".join(f"lang={p['lang']!r}: '{p['text'][:40]}'" for p in wrong[:5]))
    if short:
        ctx.review(f"{len(short)} short text(s) are in a different script from their declared language; inspect_element "
                   "each: a brand or product name needs no lang, a phrase or sentence does.",
                   [p["selector"] for p in short], "; ".join(f"'{p['text'][:30]}'" for p in short[:5]))
    # Same script, another language (French in an English page): language identification on passages long enough.
    misread = [(p, found) for p in page.get("passages", []) if p not in wrong
               and (found := _other_language(p["text"], p["lang"]))]
    if misread:
        ctx.fail(f"{len(misread)} passage(s) are in another language than the lang they inherit (language identification).",
                 [p["selector"] for p, _ in misread],
                 "; ".join(f"lang={p['lang']!r} but '{found}': '{p['text'][:40]}'" for p, found in misread[:5]))
    marked = sorted({p["lang"] for p in page.get("passages", []) if p["lang"] and p["lang"] != page.get("page_lang")})
    ctx.review("Check passages in other Latin-script languages are marked with lang."
               + (f" Languages already marked: {', '.join(marked)}." if marked else ""))


@wcag_tool("3.1.3", "check_unusual_words", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_unusual_words(ctx: Ctx) -> None:
    """Judge whether jargon, idioms, or unusual words have definitions available (glossary, dfn, inline)."""
    page = ctx.data("page")
    ctx.review("Check idioms, jargon, and unusual words are defined." +
               (f" {page.get('dfn_elements')} <dfn> element(s) found." if page.get("dfn_elements") else ""))


@wcag_tool("3.1.4", "check_abbreviations", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_abbreviations(ctx: Ctx) -> None:
    """Abbreviations need an expansion (abbr title, first-use expansion, or glossary)."""
    page = ctx.data("page")
    body = page.get("main_text", "")
    found = Counter(m.group(0).rstrip("s") for m in ACRONYM.finditer(body))
    # An all-caps word that also appears in lower or title case (HOME in a menu, "home" in the text) is a styled
    # word, not an abbreviation.
    def styled_word(a: str) -> bool:
        return any(m.group(0) != a for m in re.finditer(rf"\b{a}\b", body, re.I))

    unexplained = [a for a in found if a not in COMMON_ACRONYMS and not re.search(rf"{a}\s*\(|\(\s*{a}\s*\)", body)
                   and not styled_word(a)]
    if unexplained:
        ctx.review(f"{len(unexplained)} abbreviation(s) without an obvious expansion: {', '.join(sorted(unexplained)[:12])}."
                   + (f" ({page.get('abbr_elements')} <abbr title> found.)" if page.get("abbr_elements") else ""))
    else:
        ctx.passed("No unexplained abbreviations found.")


@wcag_tool("3.1.5", "check_reading_level", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_reading_level(ctx: Ctx) -> None:
    """Text needing more than lower-secondary reading ability needs a simpler version or supplement."""
    page = ctx.data("page")
    lang, text = (page.get("page_lang") or "").lower(), page.get("main_text", "")
    # Flesch-Kincaid is for English: elsewhere word counts and syllables mean nothing (Japanese has no spaces).
    if (lang and not lang.startswith("en")) or (not lang and _script(text[:1500]) not in (None, "Latin")):
        ctx.review(f"The page's language is {lang or _script(text[:1500])}, which the readability formula doesn't cover; "
                   "judge from a screenshot whether the main text needs more than lower-secondary reading ability.")
        return
    grade, words = flesch_kincaid_grade(text)
    if words < 100:
        ctx.not_applicable(f"Only {words} words of main content; too little to assess reading level.")
    elif grade > 9:
        ctx.review(f"Flesch-Kincaid grade {grade} (above lower secondary, about grade 9); check for a summary, "
                   "simpler version, or illustrations.", evidence=f"{words} words")
    else:
        ctx.passed(f"Flesch-Kincaid grade {grade} over {words} words (at or below lower secondary).")


@wcag_tool("3.1.6", "check_pronunciation_support", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_pronunciation_support(ctx: Ctx) -> None:
    """Where meaning depends on pronunciation (e.g. heteronyms, some CJK text), pronunciation must be available."""
    page = ctx.data("page")
    ctx.review("Check words whose meaning depends on pronunciation have it provided."
               + (f" {page.get('ruby_elements')} <ruby> element(s) found." if page.get("ruby_elements") else ""))


# ---------------------------------------------------------------- 3.2 Predictable

@wcag_tool("3.2.1", "check_context_changes_on_focus", domain="navigation", tech=(PW,), uses=("keyboard",))
def check_context_changes_on_focus(ctx: Ctx) -> None:
    """Moving focus must not change the context (navigate, open windows, move focus elsewhere)."""
    kb = ctx.data("keyboard")
    if not kb:
        return
    removed, navigations = kb["focus_removed"], kb["focus_navigations"]
    if removed:  # W3C failure F55
        ctx.fail(f"Receiving focus changes the context for {len(removed)} element(s): a script moves focus away from them.",
                 [s["selector"] for s in removed], "; ".join(f"{s['tag']} '{s['name']}'" for s in removed[:5]))
    if navigations:
        ctx.fail(f"Moving keyboard focus onto {len(navigations)} element(s) tried to navigate to another page.",
                 [s["selector"] for s in navigations],
                 "; ".join(f"{s['tag']} '{s['name']}' -> {s['request']}" for s in navigations[:3]))
    elif not inspectable_stops(kb):
        no_stops(ctx, kb)  # e.g. a consent dialog in an iframe held focus, so nothing outside it was reached
    elif not removed:
        pass_walk(ctx, kb, "Moving focus never triggered navigation.")


@wcag_tool("3.2.2", "check_context_changes_on_input", domain="forms", tech=(PW,), uses=("forms", "instrumentation"))
def check_context_changes_on_input(ctx: Ctx) -> None:
    """Changing a setting must not change the context unless users were told."""
    forms = ctx.data("forms")
    if not (fields := form_fields(forms)):
        ctx.not_applicable("No form controls on the page.")
        return
    changed = [c for change in forms.get("changes", []) for c in change["changes"]]
    choice = {f["selector"] for f in fields if f["tag"] == "select" or f["type"] in ("checkbox", "radio")}
    untested = sorted(choice - {c["control"] for c in changed})
    listeners = ctx.data("instrumentation").get("listeners", {})
    listened = {*listeners.get("change", []), *listeners.get("input", [])}   # script listeners, not only onchange=
    with_handler = [f["selector"] for f in fields if f["selector"] not in choice
                    and (f.get("change_handler") or f["selector"] in listened)]
    if context_changes := _context_changes(forms):
        ctx.fail(f"Changing {len(context_changes)} control(s) navigates away without the user submitting.",
                 [c["control"] for c in context_changes],
                 "; ".join(f"{c['control']} -> {(c['navigation'] or ['URL changed'])[0]}" for c in context_changes[:3]))
    if untested:
        ctx.review(f"{len(untested)} select/checkbox/radio control(s) were not changed by the automated test; change "
                   "each (fill_field, click_element) and watch for navigation, a new window or focus moving.", untested)
    if with_handler:  # text fields, which the probe doesn't change: a change or input handler is the sign to look at
        ctx.review(f"{len(with_handler)} text field(s) have a change or input handler; inspect_element each to see "
                   "whether changing the value navigates, submits, or moves focus.", with_handler)
    if not ctx.findings:
        ctx.passed(f"Changing {len(changed)} select/checkbox/radio control(s) caused no navigation." if changed else
                   "Only text fields, none with a change or input handler; no select, checkbox, or radio to change.")


def _order_conflicts(a: list[str], b: list[str]) -> list[str]:
    """Items present in both lists whose relative order differs."""
    common = [x for x in a if x in b]
    in_b = sorted(common, key=b.index)
    return [x for x, y in zip(common, in_b) if x != y]


def _site_pages(ctx: Ctx) -> list[str]:
    return [p for p in ctx.audit.pages if p in ctx.audit.nav_snapshots]


def _comparable_pages(ctx: Ctx) -> list[str] | None:
    """The audited pages to compare (3.2.3, 3.2.4, 3.2.6), or None after recording that there are fewer than two."""
    pages = _site_pages(ctx)
    if len(pages) < 2:
        ctx.review("Needs at least two audited pages to compare; re-runs automatically at the end of a multi-page audit. "
                   "In a single-page audit, record needs_review with that reason and note the repeated components seen.")
        return None
    return pages


def _link_names(snap: dict) -> dict[str, set[str]]:
    """Destination -> the names it is linked with on one page ("" for a link with no accessible name)."""
    names: dict[str, set[str]] = {}
    for link in snap["all_links"]:
        names.setdefault(urldefrag(link["href"])[0].rstrip("/"), set()).add(link["text"])
    return names


def _compare_order(ctx: Ctx, items, what: str) -> None:
    """Compare the order of `items(snapshot)` on this page with the first audited page's (3.2.3, 3.2.6)."""
    if (pages := _comparable_pages(ctx)) is None:
        return
    snaps, first = ctx.audit.nav_snapshots, pages[0]
    conflicts = {p: c for p in pages[1:] if (c := _order_conflicts(items(snaps[first]), items(snaps[p])))}
    if ctx.page_key in conflicts:
        ctx.fail(f"{what} appear in a different order than on {first}.", evidence=", ".join(conflicts[ctx.page_key][:8]))
    elif not items(snaps[first]):
        ctx.review(f"No {what.lower()} were identified on {first}, so nothing could be compared automatically; "
                   "check the accessibility tree for repeated link blocks on these pages and compare their order.")
    elif not conflicts:
        ctx.passed(f"{what} keep the same order across {len(pages)} pages.")


@wcag_tool("3.2.3", "check_consistent_navigation", domain="navigation", tech=(PW,), uses=("nav",))
def check_consistent_navigation(ctx: Ctx) -> None:
    """Repeated navigation must appear in the same relative order across pages."""
    _compare_order(ctx, primary_nav, "Navigation links")


@wcag_tool("3.2.4", "check_consistent_identification", domain="navigation", tech=(PW,), uses=("nav",), judgement=True)
def check_consistent_identification(ctx: Ctx) -> None:
    """Components with the same function must be identified consistently across pages."""
    if (pages := _comparable_pages(ctx)) is None:
        return
    # Links to the same destination on several pages should carry the same names on each page.
    here = ctx.page_key
    per_page = {p: _link_names(ctx.audit.nav_snapshots[p]) for p in pages}
    mine = per_page.get(here, {})
    # A repeated component is one on every audited page (header, nav, footer); two articles that happen to link
    # the same page under different titles are content, not a component.
    repeated = [h for h in mine if all(h in per_page[p] for p in pages if p != here)]
    unnamed = [h for h in repeated if "" in mine[h]]
    # Compare each page's own set of names, so a destination linked twice per page (logo + "Home") isn't a conflict.
    # A destination is renamed only when two pages share no name for it at all; a page that adds a second link to
    # the same place (logo + "Home", or "Home" + "Inaccessible Home Page") still identifies it consistently.
    def disjoint(h: str) -> bool:
        sets = [per_page[p][h] - {""} for p in pages if h in per_page[p]]
        return any(a and b and a.isdisjoint(b) for a in sets for b in sets)

    renamed = {h: sorted({n for p in pages for n in per_page[p].get(h, set()) if n}) for h in repeated if disjoint(h)}
    if unnamed:
        ctx.review(f"{len(unnamed)} link(s) that repeat across pages have no accessible name here, so the component "
                   "isn't identified at all (image links without alt text).", evidence="; ".join(unnamed[:5]))
    if renamed:
        ctx.review(f"{len(renamed)} destination(s) are linked with different names on different pages.",
                   evidence="; ".join(f"{h}: {' / '.join(n)}" for h, n in list(renamed.items())[:5]))
    if not repeated:
        ctx.review("No links repeat across the audited pages; check other repeated components (icons, buttons, "
                   "search) are identified consistently.")
    elif not ctx.findings:
        ctx.passed(f"{len(repeated)} link destination(s) that repeat across pages use the same names on each page.")


@wcag_tool("3.2.5", "check_change_on_request", domain="navigation", tech=(PW,), uses=("page", "instrumentation", "forms"),
           judgement=True)
def check_change_on_request(ctx: Ctx) -> None:
    """At AAA, context changes happen only on user request (no auto-redirects, unannounced new windows)."""
    page, inst = ctx.data("page"), ctx.data("instrumentation")
    unwarned = [l for l in page.get("links", []) if l["new_window"] and not l["warns_new_window"]]
    if page.get("meta_refresh"):
        ctx.fail(f"The page refreshes or redirects automatically (meta refresh '{page['meta_refresh']}').")
    if inst.get("window_open_calls"):
        ctx.fail(f"The page opened {inst['window_open_calls']} window(s) without a request.")
    if unwarned:
        ctx.review(f"{len(unwarned)} link(s) open a new window without saying so.", [l["selector"] for l in unwarned],
                   "; ".join(f"'{l['text'][:30]}'" for l in unwarned[:5]))
    if context_changes := _context_changes(ctx.data("forms")):
        ctx.fail("Changing a form control navigates away.", [c["control"] for c in context_changes])
    if not ctx.findings:
        ctx.passed("No automatic redirects, pop-ups, or unannounced new windows found.")


@wcag_tool("3.2.6", "check_consistent_help", domain="navigation", tech=(PW,), uses=("nav", "page"))
def check_consistent_help(ctx: Ctx) -> None:
    """Help mechanisms repeated across pages must be in the same relative order."""
    if not ctx.data("nav").get("help") and len(_site_pages(ctx)) < 2:
        ctx.not_applicable("No help mechanism (contact, help, chat, FAQ) found on this page.")
        return
    _compare_order(ctx, lambda s: [h["text"] for h in s["help"]], "Help links")


# ---------------------------------------------------------------- 3.3 Input Assistance

def _forms_or_na(ctx: Ctx) -> dict | None:
    forms = ctx.data("forms")
    if not form_fields(forms):
        ctx.not_applicable("No forms on the page.")
        return None
    return forms


@wcag_tool("3.3.1", "check_error_identification", domain="forms", tech=(PW,), uses=("forms",))
def check_error_identification(ctx: Ctx) -> None:
    """Detected input errors must be identified and described in text."""
    if (forms := _forms_or_na(ctx)) is None:
        return
    for form, reason in untested_submissions(forms):
        ctx.review(f"The automated submission of {'the controls outside a form' if form in (None, 'body') else form} "
                   f"didn't run ({reason}); call "
                   "test_forms on it, or inspect_element its fields and submit control to judge how errors are reported.",
                   [form] if form else [])
    for s in completed_submissions(forms):
        fx, form, mode = submission_facts(s), [s["form"]] if s["form"] else [], s["mode"]
        if fx["identified"]:
            how = "browser validation messages" if fx["native"] and not fx["shown"] else "text shown on the page"
            ctx.passed(f"Submitting {s['form'] or 'the form'} {mode} identified the errors ({how}).",
                       [a["selector"] for a in fx["aria_invalid"]] + [n["selector"] for n in fx["native"]],
                       "; ".join(fx["shown"][:3] + [n["message"] for n in fx["native"][:3]]))
            if fx["aria_invalid"] and not fx["described"]:
                ctx.review("Fields are marked aria-invalid but no error text is associated with them.",
                           [a["selector"] for a in fx["aria_invalid"]])
        elif s["tried_to_submit"]:
            ctx.review(f"The form tried to submit {mode} values with no client-side message; server-side errors were not "
                       "tested because submission was blocked. Record needs_review with that reason unless inspect_element "
                       "shows the fields need no validation.", form)
        elif s["has_required"]:
            # Required fields + a refused submission = the form detected an error but never described it.
            ctx.fail(f"Submitting the form {mode} did nothing visible: no error message and no submission, although it "
                     "has required fields.", form, f"Submit method: {s['submit']}.")
        else:
            # 3.3.1 applies once an input error is detected; a form with no required fields (a site search, say) may
            # simply ignore empty input, so an agent must judge it.
            ctx.review(f"Submitting the form {mode} did nothing visible: no error message and no submission. It has no "
                       "required fields; inspect_element its fields (pattern, type, aria-required) to judge whether it "
                       "detected an error without describing it.", form, f"Submit method: {s['submit']}.")


@wcag_tool("3.3.2", "check_labels_or_instructions", domain="forms", tech=(AXE, PW, SR), uses=("forms", "screen_reader"),
           judgement=True)
def check_labels_or_instructions(ctx: Ctx) -> None:
    """Inputs need visible labels or instructions (a placeholder alone is not enough)."""
    forms = _forms_or_na(ctx)
    if forms is None:
        return
    fields = form_fields(forms)
    no_label = [f for f in fields if not f["visible_label"] and f["type"] not in ("submit", "button", "hidden")]
    placeholder_only = [f for f in no_label if f["placeholder"]]
    if placeholder_only:
        ctx.fail(f"{len(placeholder_only)} field(s) rely on placeholder text instead of a visible label.",
                 [f["selector"] for f in placeholder_only], "; ".join(f"placeholder '{f['placeholder']}'" for f in placeholder_only[:5]))
    unlabeled = [f for f in no_label if not f["placeholder"] and not f["label"]]
    if unlabeled:
        ctx.fail(f"{len(unlabeled)} field(s) have no label or instructions at all.", [f["selector"] for f in unlabeled])
    sr = ctx.data("screen_reader")
    nameless = [f["spoken"] for f in sr.get("focus", []) if re.match(r"^(edit|combo box|check box|radio button)\b", f["spoken"] or "")]
    if nameless:
        ctx.fail(f"The screen reader announces {len(nameless)} field(s) with no label.", evidence="; ".join(nameless[:5]))
    rest = [f for f in no_label if f not in placeholder_only and f not in unlabeled]
    if rest:
        ctx.review(f"{len(rest)} field(s) are labelled only for assistive technology (aria-label or title); check sighted "
                   "users get instructions.",
                   [f["selector"] for f in rest])
    # A typed date with no stated format (the eval's booking_1 false pass): only a person can tell if any format works.
    undated = [f for f in fields if f["type"] in ("text", "tel") and DATE_FIELD.search(f"{f['label']} {f['name']} {f['autocomplete']}")
               and not FORMAT_HINT.search(f"{f['label']} {f['placeholder']} {f.get('hint')}")]
    if undated:
        ctx.review(f"{len(undated)} date field(s) don't say what format to enter (e.g. DD/MM/YYYY); if the field "
                   "needs a format, the label or a hint must state it.",
                   [f["selector"] for f in undated], "; ".join(f"'{f['label']}'" for f in undated[:5]))
    if not ctx.findings:
        ctx.passed(f"All {len(fields)} fields have visible labels.")


@wcag_tool("3.3.3", "check_error_suggestions", domain="forms", tech=(PW,), uses=("forms", "page"), judgement=True)
def check_error_suggestions(ctx: Ctx) -> None:
    """Judge whether error messages say how to fix the input."""
    forms = _forms_or_na(ctx)
    if forms is None:
        return
    facts = [submission_facts(s) for s in completed_submissions(forms)]
    messages = [m for fx in facts for m in fx["shown"]]
    native = [n["message"] for fx in facts for n in fx["native"]]
    if messages:
        helpful = [m for m in messages if HELP_HINT.search(m)]
        if helpful:
            ctx.passed(f"{len(helpful)} error message(s) suggest a fix.", evidence="; ".join(helpful[:4]))
        ctx.review("Check each error message explains how to correct the input.", evidence="; ".join(dict.fromkeys(messages[:6])))
    elif native:
        ctx.review("Errors are only reported by browser validation bubbles; check they're specific enough.", evidence="; ".join(native[:4]))
    else:
        ctx.review("No error messages were shown during testing, so suggestions couldn't be assessed.")


@wcag_tool("3.3.4", "check_error_prevention_legal_financial_data", domain="forms", tech=(PW,), uses=("forms", "page"),
           judgement=True)
def check_error_prevention_legal_financial_data(ctx: Ctx) -> None:
    """Legal, financial, data-changing, or test submissions must be reversible, checked, or confirmed."""
    forms = _forms_or_na(ctx)
    if forms is None:
        return
    labels = " ".join(f"{f['label']} {f['name']}" for f in form_fields(forms))
    submits = " ".join(form["submit"]["name"] for form in forms["forms"] if form.get("submit"))
    if RISKY_FORM.search(labels) or RISKY_FORM.search(submits):
        ctx.review("This form looks legal, financial, or data-changing; check for review, confirm, or undo before it's final.")
    else:
        ctx.not_applicable("The forms don't appear to make legal or financial commitments or change stored data.")


@wcag_tool("3.3.5", "check_context_sensitive_help", domain="forms", tech=(PW,), uses=("forms",), judgement=True)
def check_context_sensitive_help(ctx: Ctx) -> None:
    """At AAA, context-sensitive help (hints, examples, help links) should be available for inputs."""
    if _forms_or_na(ctx) is not None:
        ctx.review("Check fields that need it have help text, examples, or a help link.")


@wcag_tool("3.3.6", "check_error_prevention_all", domain="forms", tech=(PW,), uses=("forms",), judgement=True)
def check_error_prevention_all(ctx: Ctx) -> None:
    """At AAA, every submission must be reversible, checked, or confirmed."""
    if _forms_or_na(ctx) is not None:
        ctx.review("Check every form submission can be reviewed, confirmed, or undone.")


@wcag_tool("3.3.7", "check_redundant_entry", domain="forms", tech=(PW,), uses=("forms", "page"), judgement=True)
def check_redundant_entry(ctx: Ctx) -> None:
    """Information already entered in a process must be auto-filled or selectable, not re-typed."""
    forms = _forms_or_na(ctx)
    if forms is None:
        return
    body = ctx.data("page").get("body_text", "")
    names = Counter(f["name"] for f in form_fields(forms) if f["name"])
    repeated = [n for n, c in names.items() if c > 1]
    if STEPS.search(body) or repeated:
        ctx.review("This looks like a multi-step process; check information isn't asked for twice."
                   + (f" Repeated field names: {', '.join(repeated[:5])}." if repeated else ""))
    else:
        ctx.passed("Single-step form with no repeated fields.")


def _authentication(ctx: Ctx, enhanced: bool) -> None:
    page = ctx.data("page")
    pw = page.get("password_fields", [])
    if not pw and not page.get("captcha"):
        ctx.not_applicable("No sign-in or CAPTCHA on the page.")
        return
    blocked = [p for p in pw if p["blocks_paste"]]
    if blocked:
        ctx.fail("A password field blocks pasting, which breaks password managers.", [p["selector"] for p in blocked])
    no_autocomplete = [p for p in pw if not p["autocomplete"]]
    if no_autocomplete:
        ctx.review("Password field(s) have no autocomplete token (current-password/new-password); check password managers can fill them.",
                   [p["selector"] for p in no_autocomplete])
    if page.get("captcha"):
        if enhanced:
            ctx.fail("A CAPTCHA is used; at AAA there is no exception for object-recognition tests.")
        else:
            ctx.review("A CAPTCHA is present; check there's an alternative or it only asks to recognise objects or the user's own content.")
    if not ctx.findings:
        ctx.passed("Sign-in allows pasting and password managers.")


@wcag_tool("3.3.8", "check_accessible_authentication_minimum", domain="forms", tech=(PW,), uses=("page",), judgement=True)
def check_accessible_authentication_minimum(ctx: Ctx) -> None:
    """Sign-in must not need a cognitive test (remembering, transcribing, puzzles) without an alternative."""
    _authentication(ctx, enhanced=False)


@wcag_tool("3.3.9", "check_accessible_authentication_enhanced", domain="forms", tech=(PW,), uses=("page",), judgement=True)
def check_accessible_authentication_enhanced(ctx: Ctx) -> None:
    """At AAA, no cognitive function test at all, including object recognition."""
    _authentication(ctx, enhanced=True)
