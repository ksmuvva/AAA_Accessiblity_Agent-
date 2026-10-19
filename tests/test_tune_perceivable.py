"""Lane L3: the perceivable rules (1.x) detect the documented failures in the p_*.html fixtures, say what to judge
when they can't decide, and never record only the "no evidence" placeholder when the page has nothing relevant."""

import pytest

from audit_helpers import found, judge, statuses
from agent_access.models import Status

PLACEHOLDER = "No automated evidence either way"


def descriptions(audit, criterion, status):
    return [f.description for f in found(audit, criterion).get(status, [])]


def selectors(audit, criterion, status):
    return {s for f in found(audit, criterion).get(status, []) for s in f.selectors}


# ---------------------------------------------------------------- 1.1.1

@pytest.fixture(scope="module")
def alternatives():
    return judge("p_text_alternatives.html", "1.1.1", "1.4.5")


def test_generic_alt_text_fails(alternatives):
    fails = selectors(alternatives, "1.1.1", Status.FAIL)
    assert {"#generic", "#logo"} <= fails and "#ok" not in fails


def test_very_long_alt_text_is_flagged_for_judgement(alternatives):
    assert "#long" in selectors(alternatives, "1.1.1", Status.NEEDS_REVIEW)
    assert any("over 150 characters" in d for d in descriptions(alternatives, "1.1.1", Status.NEEDS_REVIEW))


def test_page_without_images_is_not_applicable_rather_than_unjudged():
    html = ("<html lang='en'><head><title>Plain</title></head><body><main><h1>Plain</h1>"
            "<p>Nothing but text on this page.</p></main></body></html>")
    audit = judge(html, "1.1.1")
    assert statuses(audit, "1.1.1") == {Status.NOT_APPLICABLE}
    assert not any(PLACEHOLDER in f.description for f in audit.findings)


# ---------------------------------------------------------------- 1.3.1 / 1.3.2

@pytest.fixture(scope="module")
def structure():
    return judge("p_structure.html", "1.3.1", "1.3.2")


def test_bold_only_table_headers_fail(structure):
    assert "#prices" in selectors(structure, "1.3.1", Status.FAIL)
    assert "#hours" not in selectors(structure, "1.3.1", Status.FAIL) | selectors(structure, "1.3.1", Status.NEEDS_REVIEW)


def test_headerless_table_without_cue_goes_to_the_agent(structure):
    assert "#plain" in selectors(structure, "1.3.1", Status.NEEDS_REVIEW)
    assert "#plain" not in selectors(structure, "1.3.1", Status.FAIL)


def test_fake_lists_and_space_columns_go_to_the_agent(structure):
    review = selectors(structure, "1.3.1", Status.NEEDS_REVIEW)
    assert {"#fakelist", "#columns"} <= review


def test_radios_with_no_group_label_go_to_the_agent():
    """The eval's booking_1 false pass: an auditor said the seat-class radios were grouped; they had no group label.
    A fieldset with a legend or a named radiogroup labels a set; a fieldset with no legend doesn't."""
    html =('<!doctype html><html lang="en"><head><title>Book</title></head><body><main><h1>Book</h1><form>'
            '<p>Seat class</p><p><input type="radio" id="std" name="cls"><label for="std">Standard</label>'
            '<input type="radio" id="fst" name="cls"><label for="fst">First</label></p>'
            '<fieldset><legend>Meal</legend><input type="radio" name="meal" id="m1"><label for="m1">Yes</label>'
            '<input type="radio" name="meal" id="m2"><label for="m2">No</label></fieldset>'
            '<div role="radiogroup" aria-label="Seat"><input type="radio" name="seat"><input type="radio" name="seat"></div>'
            '<fieldset><input type="radio" name="bag" id="b1"><label for="b1">Yes</label>'
            '<input type="radio" name="bag" id="b2"><label for="b2">No</label></fieldset></form></main></body></html>')
    review = [f for f in found(judge(html, "1.3.1"), "1.3.1")[Status.NEEDS_REVIEW] if "radio" in f.description]
    assert len(review) == 1 and review[0].evidence == "cls: Standard, First; bag: Yes, No" and len(review[0].selectors) == 2


def test_spaced_out_letters_fail_meaningful_sequence(structure):
    assert "#spaced" in selectors(structure, "1.3.2", Status.FAIL)
    assert any("W E L C O M E" in (f.evidence or "") for f in found(structure, "1.3.2")[Status.FAIL])


def test_structure_rules_leave_evidence_for_the_agent(structure):
    details = structure.rule_details[structure.current_url]
    assert any(t["selector"] == "#prices" and not t["has_headers"] for t in details["check_info_and_relationships"]["tables"])
    assert details["check_meaningful_sequence"]["reading_order"]


# ---------------------------------------------------------------- 1.4.1

@pytest.fixture(scope="module")
def colour_links():
    return judge("p_colour_links.html", "1.4.1")


def test_colour_only_link_under_three_to_one_fails(colour_links):
    fails = found(colour_links, "1.4.1").get(Status.FAIL, [])
    assert fails and any("#faint" in s for f in fails for s in f.selectors)


def test_colour_only_link_over_three_to_one_needs_hover_focus_check(colour_links):
    review = [f for f in found(colour_links, "1.4.1").get(Status.NEEDS_REVIEW, []) if "#bright" in f.selectors]
    assert review and "hover" in review[0].description
    assert "#underlined" not in selectors(colour_links, "1.4.1", Status.NEEDS_REVIEW) | selectors(colour_links, "1.4.1", Status.FAIL)


def test_colour_only_check_does_not_double_count_axe():
    """When axe already reports the link, the custom check adds nothing but the ratio in details."""
    audit = judge("p_colour_links.html", "1.4.1")
    fails = found(audit, "1.4.1")[Status.FAIL]
    assert len([f for f in fails if "#faint" in " ".join(f.selectors)]) == 1
    links = audit.rule_details[audit.current_url]["check_use_of_color"]["undecorated_text_links"]
    assert {l["selector"] for l in links} == {"#faint", "#bright"}


# ---------------------------------------------------------------- 1.4.2 / 1.2.x evidence

def test_embedded_frames_without_media_elements_are_not_left_unjudged():
    audit = judge("iframe_page.html", "1.4.2")
    assert statuses(audit, "1.4.2") == {Status.NEEDS_REVIEW}
    assert not any(PLACEHOLDER in f.description for f in audit.findings)


def test_media_rules_record_track_and_control_details():
    audit = judge("media_page.html", "1.2.2", "1.4.2")
    details = audit.rule_details[audit.current_url]
    media = {m["selector"]: m for m in details["check_prerecorded_captions"]["media"]}
    assert media["#promo"]["autoplay"] and not media["#promo"]["controls"] and media["#promo"]["tracks"] == []
    assert media["#talk"]["tracks"][0]["kind"] == "captions"
    assert details["check_audio_control"]["media"]


def test_caption_cues_are_read():
    """1.2.2 used to pass any video with a captions track. Its cues are now read from a one-minute video: an empty
    track or one that can't be fetched fails, a track that stops halfway is a review naming the gap, a full one passes,
    and a track an inline page can't resolve stays a review."""
    import base64
    from urllib.parse import quote

    from audit_helpers import FIXTURES

    video = "data:video/webm;base64," + base64.b64encode((FIXTURES / "minute.webm").read_bytes()).decode()

    def captions(*srcs):
        tracks = "".join(f'<track kind="captions" srclang="en" src="{src}">' for src in srcs)
        audit = judge(f'<html lang="en"><title>Talk</title><main><h1>Talk</h1><video id="talk" controls src="{video}">'
                      f'{tracks}</video></main></html>', "1.2.2")
        return {s: [f.evidence or f.description for f in fs] for s, fs in found(audit, "1.2.2").items()}

    def vtt(*spans):
        stamp = lambda s: f"{s // 60:02d}:{s % 60:02d}.000"
        return "data:text/vtt," + quote("WEBVTT\n\n" + "".join(f"{stamp(a)} --> {stamp(b)}\nWords\n\n" for a, b in spans))

    assert set(captions(vtt((0, 20), (20, 40), (40, 60)))) == {Status.PASS}
    assert Status.FAIL in captions(vtt()) and Status.FAIL in captions("http://127.0.0.1:9/talk.vtt")
    assert captions(vtt((0, 30)))[Status.NEEDS_REVIEW] == ["#talk: 30-60s"]
    assert set(captions(vtt((0, 10), (10, 20), (20, 30)), vtt((0, 60)))) == {Status.PASS}  # coverage, not cue count
    assert Status.NEEDS_REVIEW in (unresolved := captions("talk.vtt")) and Status.FAIL not in unresolved


# ---------------------------------------------------------------- 1.4.5 / 1.4.8 / 1.4.11

def test_images_of_text_names_the_suspects(alternatives):
    review = found(alternatives, "1.4.5")[Status.NEEDS_REVIEW]
    assert "#long" in review[0].selectors  # sentence-like alt text
    assert "take_screenshot" in review[0].description


def test_justified_and_overlong_lines_fail_visual_presentation():
    audit = judge("p_visual_presentation.html", "1.4.8")
    assert "#justified" in selectors(audit, "1.4.8", Status.FAIL)
    assert "#wide" in selectors(audit, "1.4.8", Status.NEEDS_REVIEW)


def test_low_contrast_control_boundary_and_icon_fail():
    audit = judge("p_non_text_contrast.html", "1.4.11")
    fails = selectors(audit, "1.4.11", Status.FAIL)
    assert {"#q", "#go"} <= fails and "#strong" not in fails


# ---------------------------------------------------------------- no false alarms

def test_clean_page_has_no_new_perceivable_failures():
    audit = judge("good_page.html", "1.1.1", "1.3.1", "1.3.2", "1.4.1", "1.4.8", "1.4.11")
    assert not [f for f in audit.findings if f.status is Status.FAIL], [f.description for f in audit.findings if f.status is Status.FAIL]


# ---------------------------------------------------------------- the axe probe must not pollute the instrumentation probe

def test_axe_injection_does_not_count_as_page_listeners():
    """axe-core adds window listeners when injected; they must not show up as the page's own (2.5.1, 2.1.x signals)."""
    from agent_access.probes import collect

    async def axe_then_instrumentation(audit):
        await collect(audit, "axe")
        return (await collect(audit, "instrumentation")).data

    from audit_helpers import judge_then
    _, instr = judge_then("good_page.html", then=axe_then_instrumentation)
    assert instr["installed"]
    assert not [t for t, where in instr["listeners"].items() if "window" in where], instr["listeners"]


# ---------------------------------------------------------------- 1.3.5

def test_invalid_autocomplete_token_fails_input_purpose():
    html = ("<html lang='en'><head><title>Sign up</title></head><body><main><h1>Sign up</h1><form>"
            "<label for='e'>Email</label><input id='e' type='email' autocomplete='foo'>"
            "<label for='n'>Your name</label><input id='n' type='text'><button>Join</button></form></main></body></html>")
    audit = judge(html, "1.3.5")
    assert "#e" in selectors(audit, "1.3.5", Status.FAIL)
    assert "#n" in selectors(audit, "1.3.5", Status.NEEDS_REVIEW)  # personal data without a token: the agent judges


def test_faded_text_fails_and_unmeasured_text_is_never_passed_over():
    faded = judge('<!doctype html><html lang="en"><head><title>Faded</title></head><body><main><h1>Faded</h1>'
                  '<div style="opacity:0.3"><p>Faded text</p></div></main></body></html>', "1.4.3")
    assert "1 text element(s) are below the AA contrast ratio." in descriptions(faded, "1.4.3", Status.FAIL)
    # Opacity fades the box's white background too: black text on it over a black page is about 5.3:1, a pass.
    box = judge('<!doctype html><html lang="en"><head><title>Box</title></head><body style="background:#000"><main>'
                '<h1 style="color:#fff">Box</h1><div style="background:#fff;opacity:.5"><p>Black text</p></div></main>'
                '</body></html>', "1.4.3")
    assert not [f for f in found(box, "1.4.3").get(Status.FAIL, []) if f.source != "axe"]
    # The body's background paints the canvas, which the body's opacity doesn't fade: grey text on black, about 5.3:1.
    body = judge('<!doctype html><html lang="en"><head><title>Body</title></head><body style="background:#000;color:#fff;'
                 'opacity:.5"><main><h1>Body</h1><p>White text</p></main></body></html>', "1.4.3")
    assert not [f for f in found(body, "1.4.3").get(Status.FAIL, []) if f.source != "axe"]
    # Containment stops that propagation: the black then fades with the body over white, and white text on it fails.
    contained = judge('<!doctype html><html lang="en"><head><title>Body</title></head><body style="contain:paint;'
                      'background:#000;color:#fff;opacity:.5"><main><h1>Body</h1><p>White text</p></main></body></html>', "1.4.3")
    assert [f for f in found(contained, "1.4.3").get(Status.FAIL, []) if f.source != "axe"]
    # A colour format the composite can't read (oklch) is never guessed into a 1:1 failure.
    oklch = judge('<!doctype html><html lang="en"><head><title>Oklch</title></head><body style="background:#000"><main>'
                  '<h1 style="color:#fff">Oklch</h1><p style="color:oklch(1 0 0);opacity:.9">Light text</p></main></body></html>',
                  "1.4.3")
    assert not [f for f in found(oklch, "1.4.3").get(Status.FAIL, []) if f.source != "axe"]
    from test_tune_operable import _rule_on
    over_image = [{"selector": "#hero h2", "text": "Summer sale", "why": "over an image or gradient, past the sampling limit"}]
    for criterion in ("1.4.3", "1.4.6"):
        findings = _rule_on(criterion, contrast={"text": [], "unmeasured_text": over_image})
        assert {f.status for f in findings} == {Status.NEEDS_REVIEW} and "#hero h2" in findings[0].selectors, criterion


def test_frames_and_shadow_roots_are_measured_with_selectors_the_tools_can_use():
    from audit_helpers import judge_then
    html = """<!doctype html><html lang="en"><head><title>Offers</title></head><body><main><h1>Offers</h1>
<iframe title="Offers" srcdoc="<!doctype html><html lang=en><body><img src=a.png></body></html>"></iframe>
<my-card></my-card><script>customElements.define('my-card', class extends HTMLElement { constructor() { super();
  this.attachShadow({mode: 'open'}).innerHTML = '<img src=b.png><a href=/x>More offers</a>'; } });</script>
</main></body></html>"""

    async def resolve(audit):
        page = await audit.page()
        return {s: await page.locator(s).count() for f in found(audit, "1.1.1")[Status.FAIL] for s in f.selectors}
    audit, counts = judge_then(html, "1.1.1", "2.4.4", then=resolve)
    assert counts == {"iframe >> internal:control=enter-frame >> img": 1, "my-card >> img": 1}
    from audit_helpers import data
    assert "html > body > main > my-card >> a" in {link["selector"] for link in data(audit, "page")["links"]}


def test_a_pop_up_shown_after_a_delay_is_still_tested():
    # It appears 0.6 s after hover, later than the fixed 0.4 s wait the probe used to make.
    html = """<!doctype html><html lang="en"><head><title>Tips</title><style>#tip{position:absolute;top:40px}</style></head>
<body><main><h1>Tips</h1><button id="b" aria-describedby="tip">Help</button><div id="tip" role="tooltip" hidden>Saved</div>
<script>let t; b.addEventListener('mouseenter', () => { t = setTimeout(() => { tip.hidden = false; }, 600); });
b.addEventListener('mouseleave', () => { clearTimeout(t); });</script></main></body></html>"""
    assert found(judge(html, "1.4.13"), "1.4.13").get(Status.FAIL)   # not dismissible with Escape


def test_only_the_page_or_its_main_content_rotating_locks_the_orientation():
    head = '<!doctype html><html lang="en"><head><title>Game</title><style>@media (orientation: portrait) {{ {} {{ transform: rotate(90deg); }} }}</style></head>'
    body = '<body><span id="arrow">&#8594;</span><main><h1>Game</h1><p>' + "Play in any orientation. " * 20 + '</p></main></body></html>'
    assert found(judge(head.format("main") + body, "1.3.4"), "1.3.4").get(Status.FAIL)
    assert not found(judge(head.format("#arrow") + body, "1.3.4"), "1.3.4").get(Status.FAIL)   # a decorative arrow


def test_modern_colour_formats_are_measured():
    # Tailwind v4 computes colours to oklch: they must be measured like rgb(), not skipped.
    html = ('<!doctype html><html lang="en"><head><title>Shop</title></head><body><main><h1>Shop</h1>'
            '<p style="color:oklch(0.7 0 0)">Grey oklch text</p><p style="color:color(display-p3 0.6 0.6 0.6)">P3 text</p>'
            '<label for="q">Search</label><input id="q" style="border:2px solid oklch(0.85 0 0)"></main></body></html>')
    audit = judge(html, "1.4.3", "1.4.11")
    low = {f.description for f in found(audit, "1.4.3").get(Status.FAIL, []) if f.source != "axe"}
    assert low == {"2 text element(s) are below the AA contrast ratio."}
    assert found(audit, "1.4.11").get(Status.FAIL)   # the light oklch border, 1.57:1


def test_an_instruction_that_points_at_a_control_only_by_its_shape_fails():
    """booking_1 (eval v2): 'Press the round button below to continue.' An auditor passed it as an extra cue and the
    reviewer agreed. Naming the control's label passes; position alone can follow the reading order, so it's asked."""
    page = '<html lang="en"><body><main><p>{}</p><button>Continue</button><a href="#h">Help</a></main></body></html>'
    by_shape = found(judge(page.format("Press the round button below to continue."), "1.3.3"), "1.3.3")
    named = found(judge(page.format("Press the round Continue button below."), "1.3.3"), "1.3.3")
    by_place = found(judge(page.format("Choose one of the links below."), "1.3.3"), "1.3.3")
    assert by_shape[Status.FAIL][0].evidence == "Press the round button below to continue"
    assert Status.FAIL not in named and Status.NEEDS_REVIEW in named
    assert Status.FAIL not in by_place and Status.NEEDS_REVIEW in by_place
