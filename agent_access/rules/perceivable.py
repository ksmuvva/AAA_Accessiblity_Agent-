"""Principle 1 - Perceivable: 1.1.1 to 1.4.13."""

from __future__ import annotations

import json
import re

from agent_access.models import Status
from agent_access.probes.contrast import contrast_ratio, parse_color, text_requirement
from agent_access.rules import AXE, CCA, PW, SR, Ctx, wcag_tool
from agent_access.rules.common import form_fields, has_media, has_video, media_selectors, no_media_at_all

# Alt text that is a file name, a number, or a generic word that says nothing about the image's purpose.
FILENAME_NAME = re.compile(r"\.(png|jpe?g|gif|svg|webp|bmp)$|^(img|image|dsc|photo|picture|pic|graphic|icon|logo|spacer|"
                           r"banner|button|untitled|placeholder)[-_ ]?\d*$|^\d+$", re.I)
# Image file names that usually mean the image shows text (1.4.5 / 1.4.9).
TEXT_IMAGE_SRC = re.compile(r"text|title|heading|headline|slogan|quote|tagline|caption|promo|banner", re.I)
MAX_ALT_LENGTH = 150
SENSORY = re.compile(
    r"\b(click|press|tap|select|use|see|choose)\b[^.]{0,40}\b(round|square|circular|red|green|blue|yellow|orange|"
    r"left|right|above|below|top|bottom|corner|side)\b|\b(on the (left|right)|shown (above|below)|the (red|green) "
    r"(button|link|text|field)s?)\b", re.I)
# An instruction that points at a control by its shape or colour ("press the round button below"). Naming it as well
# ("the round Continue button", "select Continue") is what 1.3.3 asks; position alone can conform when it follows the
# reading order ("the links below"), so it stays a question.
BY_LOOK = re.compile(r"\b(?:click|press|tap|select|choose)\b[^.!?]{0,40}?\b(?:round|square|circular|red|green|blue|"
                     r"yellow|orange)\b[^.!?]{0,20}?\b(?:button|link|icon|arrow)s?\b[^.!?]{0,60}", re.I)
# Text whose CSS size falls below this fraction when the viewport halves didn't scale with zoom (e.g. vw units).
MIN_SCALE_RATIO = 0.6


def _no_media(ctx: Ctx) -> bool:
    if no_media_at_all(ctx.data("media")):
        ctx.not_applicable("The page has no video, audio, embedded players, iframes, objects, or canvas.")
        return True
    return False


def _media_or_na(ctx: Ctx, need_video: bool = True) -> dict | None:
    """The media data, or None after recording not applicable when there's no video (or no media at all)."""
    if _no_media(ctx):
        return None
    m = ctx.data("media")
    if not (has_video(m) if need_video else has_media(m)):
        ctx.not_applicable(f"No {'video' if need_video else 'audio or video'} on the page.")
        return None
    _media_details(ctx, m)
    return m


def _media_details(ctx: Ctx, m: dict) -> None:
    """What the agent needs to judge the 1.2.x / 1.4.x media criteria: each element's tracks, controls, and autoplay."""
    ctx.detail("media", [{"selector": x["selector"], "tag": x["tag"], "src": x["src"], "tracks": x["tracks"],
                          "controls": x["controls"], "autoplay": x["autoplay"], "muted": x["muted"], "loop": x["loop"],
                          "duration_s": x["duration"]} for x in m.get("videos", []) + m.get("audio", [])])
    if m.get("player_iframes"):
        ctx.detail("player_iframes", m["player_iframes"])


def _player_review(ctx: Ctx, m: dict) -> None:
    if m.get("player_iframes"):
        ctx.review(f"{len(m['player_iframes'])} embedded third-party player(s); check this in the player.",
                   [p["selector"] for p in m["player_iframes"]])


# ---------------------------------------------------------------- 1.1 Text Alternatives

@wcag_tool("1.1.1", "check_non_text_content", domain="lead", tech=(AXE, PW, SR),
           uses=("axe", "page", "screen_reader", "media"), judgement=True)
def check_non_text_content(ctx: Ctx) -> None:
    """Judge whether each image's text alternative conveys its purpose, and that decorative images are hidden."""
    ctx.axe()
    page, sr = ctx.data("page"), ctx.data("screen_reader")
    images = [i for i in page.get("images", []) if not i["hidden"]]
    bad_names = [i for i in images if i["alt"] and FILENAME_NAME.search(i["alt"].strip())]
    if bad_names:
        ctx.fail(f"{len(bad_names)} image(s) use a file name or placeholder as alt text.",
                 [i["selector"] for i in bad_names], "; ".join(f"alt='{i['alt']}'" for i in bad_names[:5]))
    unlabeled = [line for line in sr.get("reading", []) if re.match(r"^(unlabeled )?graphic\s*(,|$)", line.strip(), re.I)
                 or re.search(r"\bgraphic,\s*[\w-]+\.(png|jpe?g|gif|svg|webp)", line, re.I)]
    if unlabeled:
        ctx.fail(f"The screen reader announces {len(unlabeled)} graphic(s) with no meaningful name.", evidence="; ".join(unlabeled[:5]))
    long_alt = [i for i in images if i["alt"] and len(i["alt"]) > MAX_ALT_LENGTH]
    if long_alt:
        ctx.review(f"{len(long_alt)} image(s) have alt text over {MAX_ALT_LENGTH} characters; check whether a short alt "
                   "plus a description in the page text would serve better.", [i["selector"] for i in long_alt])
    if images or page.get("background_images"):
        ctx.review("Check every text alternative describes the image's purpose (not just 'image'), and that "
                   "informative CSS background images have a text equivalent.",
                   [i["selector"] for i in images], "; ".join(f"{i['tag']} alt={i['alt']!r}" for i in images[:10]))
    if graphics := ctx.data("media").get("canvas", 0) + ctx.data("media").get("objects", 0):
        ctx.review(f"{graphics} canvas, object or embed element(s) can show images or text no check reads; check each "
                   "has a text alternative (fallback content, aria-label, or nearby text).")
    if not ctx.findings:  # no images or graphics, nothing from axe or the screen reader
        ctx.not_applicable("No images, graphics, canvas, objects, image buttons, image maps, or CSS background images on "
                           "the page.")
    ctx.detail("images", [{"selector": i["selector"], "alt": i["alt"], "src": i["src"]} for i in images])


# ---------------------------------------------------------------- 1.2 Time-based Media

@wcag_tool("1.2.1", "check_prerecorded_audio_video_alternatives", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_prerecorded_audio_video_alternatives(ctx: Ctx) -> None:
    """For audio-only, is there a transcript? For video-only (no sound), a transcript or audio track?"""
    if (m := _media_or_na(ctx, need_video=False)) is None:
        return
    if m.get("audio") and not m.get("videos"):
        ctx.review(f"{len(m['audio'])} audio element(s); check that a transcript is provided.", [a["selector"] for a in m["audio"]])
    else:
        ctx.review("Check whether any media is audio-only or video-only; if so, confirm a transcript or alternative.",
                   media_selectors(m))


@wcag_tool("1.2.2", "check_prerecorded_captions", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_prerecorded_captions(ctx: Ctx) -> None:
    """Confirm captions exist and are accurate (track, CC button in the player, or burned-in)."""
    if (m := _media_or_na(ctx)) is None:
        return
    videos = m.get("videos", [])
    no_captions = [v for v in videos if not any(t["kind"] in ("captions", "subtitles") for t in v["tracks"])]
    if no_captions:
        ctx.review(f"{len(no_captions)} video(s) have no caption track; check for open (burned-in) captions or a transcript.",
                   [v["selector"] for v in no_captions])
    # The probe reads each caption track's cues: an empty track, or one the browser couldn't fetch, shows no captions.
    silent, unread, gappy = [], [], []
    for v in videos:
        if not (caps := [t for t in v["tracks"] if "state" in t]):
            continue
        best = min(caps, key=lambda t: (not t["cues"], len(t["gaps"]), -t["cues"]))  # cues, then fewest gaps
        if best["cues"]:
            gappy += [(v, best)] if best["gaps"] else []
        elif all(t["state"] == "loaded" or t["state"] == "error" and t["src"].startswith(("http:", "https:")) for t in caps):
            silent.append((v, caps))
        else:
            unread.append(v)
    if silent:
        ctx.fail(f"{len(silent)} video(s) have caption tracks that show no captions: each track is empty or failed to load.",
                 [v["selector"] for v, _ in silent],
                 "; ".join(f"{t['src'][:60]}: {t['state']}, {t['cues']} cues" for _, caps in silent for t in caps[:2]))
    if unread:
        ctx.review(f"{len(unread)} video(s) have caption tracks that couldn't be read here; check the captions play.",
                   [v["selector"] for v in unread])
    if gappy:
        ctx.review(f"{len(gappy)} video(s) have stretches of 30 s or more with no caption; check nothing is said there.",
                   [v["selector"] for v, _ in gappy],
                   "; ".join(f"{v['selector']}: " + ", ".join(f"{a}-{b}s" for a, b in t["gaps"]) for v, t in gappy))
    if videos and not (no_captions or silent or unread or gappy):
        ctx.passed("Every video has a captions or subtitles track with cues.")
    _player_review(ctx, m)


@wcag_tool("1.2.3", "check_audio_description_or_media_alternative", domain="media", tech=(PW,), uses=("media", "page"),
           judgement=True)
def check_audio_description_or_media_alternative(ctx: Ctx) -> None:
    """Confirm each video has audio description or a full text alternative (transcript)."""
    if (m := _media_or_na(ctx)) is None:
        return
    alternatives = [l["text"] for l in ctx.data("page").get("links", [])
                    if re.search(r"transcript|text version|audio description", l["text"], re.I)]
    ctx.review("Check each video has audio description or a media alternative."
               + (f" Possible alternatives linked: {', '.join(alternatives[:3])}." if alternatives else ""), media_selectors(m))


@wcag_tool("1.2.4", "check_live_captions", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_live_captions(ctx: Ctx) -> None:
    """For live video (streams), confirm real-time captions."""
    if (m := _media_or_na(ctx)) is None:
        return
    live = [v for v in m.get("videos", []) if v["duration"] is None and re.search(r"\.m3u8|live|stream", v["src"] or "", re.I)]
    live += [p for p in m.get("player_iframes", []) if re.search(r"live|stream|twitch", p["src"], re.I)]
    if live:
        ctx.review("Possible live stream(s); confirm real-time captions.", [x["selector"] for x in live])
    else:
        ctx.passed("No live video detected (prerecorded media is covered by 1.2.2).")


@wcag_tool("1.2.5", "check_prerecorded_audio_description", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_prerecorded_audio_description(ctx: Ctx) -> None:
    """Confirm audio description for prerecorded video whose visuals carry information not in the audio."""
    if (m := _media_or_na(ctx)) is None:
        return
    no_descriptions = [v for v in m.get("videos", []) if not any(t["kind"] == "descriptions" for t in v["tracks"])]
    if no_descriptions:
        ctx.review(f"{len(no_descriptions)} video(s) have no audio-description track; check whether the visuals carry "
                   "information not in the audio.", [v["selector"] for v in no_descriptions])
    _player_review(ctx, m)


def _media_review(ctx: Ctx, what: str, need_video: bool = True) -> None:
    if (m := _media_or_na(ctx, need_video)) is not None:
        ctx.review(what, media_selectors(m))


@wcag_tool("1.2.6", "check_prerecorded_sign_language", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_prerecorded_sign_language(ctx: Ctx) -> None:
    """Confirm sign language interpretation for prerecorded audio in video."""
    _media_review(ctx, "Check each video with audio provides sign language interpretation.")


@wcag_tool("1.2.7", "check_extended_audio_description", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_extended_audio_description(ctx: Ctx) -> None:
    """Where pauses are too short for audio description, confirm extended audio description."""
    _media_review(ctx, "Check whether extended audio description is needed and provided.")


@wcag_tool("1.2.8", "check_prerecorded_media_alternative", domain="media", tech=(PW,), uses=("media", "page"), judgement=True)
def check_prerecorded_media_alternative(ctx: Ctx) -> None:
    """Confirm a full text alternative (transcript with visual descriptions) for every prerecorded video."""
    transcripts = [l for l in ctx.data("page").get("links", []) if re.search(r"transcript|text version", l["text"], re.I)]
    _media_review(ctx, "Check each video has a full text alternative."
                  + (" Transcript links found." if transcripts else " No transcript links found."))


@wcag_tool("1.2.9", "check_live_audio_alternative", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_live_audio_alternative(ctx: Ctx) -> None:
    """For live audio-only content, confirm a text alternative (e.g. live transcript)."""
    audio = ctx.data("media").get("audio", [])
    live = [a for a in audio if a["duration"] is None]
    if not audio:
        ctx.not_applicable("No audio elements on the page.")
    elif live:
        ctx.review("Possible live audio; confirm a live text alternative.", [a["selector"] for a in live])
    else:
        ctx.passed("No live audio detected.")


# ---------------------------------------------------------------- 1.3 Adaptable

@wcag_tool("1.3.1", "check_info_and_relationships", domain="lead", tech=(AXE, PW, SR), uses=("axe", "page", "screen_reader"),
           judgement=True)
def check_info_and_relationships(ctx: Ctx) -> None:
    """Judge whether headings, lists, tables, and form groupings in the markup match the visual structure."""
    ctx.axe()
    page = ctx.data("page")
    fake = page.get("fake_headings", [])
    if fake:
        ctx.review(f"{len(fake)} piece(s) of text look like headings but aren't marked up as headings.",
                   [f["selector"] for f in fake], "; ".join(f"'{f['text']}' ({f['font_px']}px)" for f in fake[:6]))
    # F91: a data table whose header cells are only styled (bold first row/column), not marked up as <th>.
    headerless = [t for t in page.get("tables", []) if not t["has_headers"] and t["rows"] >= 2 and t["cols"] >= 2]
    styled = [t for t in headerless if t["rows"] >= 3 and (t["first_row_bold"] or t["first_col_bold"])
              and not t["first_row_has_links"] and not t["nested_tables"] and not t["in_nav"]]
    if styled:
        ctx.fail(f"{len(styled)} data table(s) have header cells that are only styled bold, with no <th>, scope, or "
                 "headers markup, so the header relationships are lost to assistive technology.",
                 [t["selector"] for t in styled], "; ".join(f"{t['selector']} ({t['rows']} rows x {t['cols']} cols)" for t in styled[:5]))
    if plain := [t for t in headerless if t not in styled]:
        ctx.review(f"{len(plain)} table(s) have no header cells at all; decide whether each is a data table (needs <th> "
                   "or scope) or a layout table (should have role=presentation). inspect_element shows its markup.",
                   [t["selector"] for t in plain], "; ".join(f"{t['selector']} ({t['rows']}x{t['cols']})" for t in plain[:5]))
    if fake_lists := page.get("fake_lists", []):
        ctx.review(f"{len(fake_lists)} block(s) look like lists made with line breaks and bullet characters rather than "
                   "<ul>/<ol>; check whether they are lists that need list markup.",
                   [f["selector"] for f in fake_lists], "; ".join(f"{f['selector']}: {f['lines']} bullet lines" for f in fake_lists[:5]))
    if pre := page.get("pre_columns", []):
        ctx.review(f"{len(pre)} <pre> block(s) use runs of spaces to lay out columns; check whether it is tabular data "
                   "that needs a table (F33/F34).", pre)
    if radios := page.get("ungrouped_radios", []):
        ctx.review(f"{len(radios)} set(s) of radio buttons have no group label (a fieldset with a legend, or a named "
                   "radiogroup): if a question or heading labels them visually, that relationship is lost (H71).",
                   [g["selector"] for g in radios], "; ".join(f"{g['name']}: {', '.join(g['options'][:4])}" for g in radios[:5]))
    ctx.detail("tables", page.get("tables", []))
    # F68: a field announced with no name has no programmatically associated label. If visible label text sits
    # next to it, the relationship is lost to assistive technology.
    sr = ctx.data("screen_reader")
    nameless = [f for f in sr.get("focus", []) if re.match(r"^(edit|combo box|check box|radio button|list)\b", f["spoken"] or "")]
    if nameless:
        ctx.review(f"{len(nameless)} form field(s) have no programmatically associated label; check whether visible "
                   "label text next to them should be associated (<label for>, aria-labelledby).",
                   [f["selector"] for f in nameless], "; ".join(f["spoken"] for f in nameless[:5]))
    ctx.detail("headings_announced", sr.get("headings", []))


@wcag_tool("1.3.2", "check_meaningful_sequence", domain="lead", tech=(PW, SR), uses=("page", "screen_reader"), judgement=True)
def check_meaningful_sequence(ctx: Ctx) -> None:
    """Compare the screen-reader reading order with the visual order (screenshot); flag where meaning changes."""
    reorder, reading = ctx.data("page").get("css_reorder", []), ctx.data("screen_reader").get("reading", [])
    # F32: letters separated by spaces to space them out; screen readers read them letter by letter.
    if spaced := ctx.data("page").get("spaced_letters", []):
        ctx.fail(f"{len(spaced)} piece(s) of text use spaces between letters for visual spacing, so screen readers "
                 "spell them out letter by letter (use CSS letter-spacing instead).",
                 [s["selector"] for s in spaced], "; ".join(f"'{s['text']}'" for s in spaced[:5]))
    if reorder:
        ctx.review(f"{len(reorder)} element(s) are visually reordered with CSS (order/reverse/dense); "
                   "check the reading order still makes sense.", reorder)
    ctx.review("Compare the reading order below with the visual layout.", evidence=" | ".join(reading[:25]))
    ctx.detail("reading_order", reading)


@wcag_tool("1.3.3", "check_sensory_characteristics", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_sensory_characteristics(ctx: Ctx) -> None:
    """Check instructions don't rely only on shape, colour, size, location, or sound."""
    page = ctx.data("page")
    text = page.get("body_text", "")
    names = {n for n in [x for link in page.get("links", []) for x in (link["text"], link["name"])]
             + page.get("button_names", []) if n}
    # A control's name, in any case, names it; except as a purpose clause: "press the round button to continue".
    by_look = sorted({m.group(0).strip() for m in BY_LOOK.finditer(text)
                      if not any(re.search(rf"(?<!\bto )(?<!\w){re.escape(n)}(?!\w)", m.group(0), re.I) for n in names)})
    matches = sorted({m.group(0) for m in SENSORY.finditer(text)})
    if by_look:
        ctx.fail(f"{len(by_look)} instruction(s) point at a control only by its shape or colour, not by its label.",
                 evidence="; ".join(by_look[:5]))
    elif matches:
        ctx.review(f"{len(matches)} instruction(s) refer to shape, colour, or position.", evidence="; ".join(matches[:8]))
    else:
        ctx.passed("No instructions referring only to shape, colour, size, or position were found.")


@wcag_tool("1.3.4", "check_orientation", domain="lead", tech=(PW,), uses=("orientation",))
def check_orientation(ctx: Ctx) -> None:
    """Content must work in both portrait and landscape."""
    o = ctx.data("orientation")
    if not o:
        return
    p, l = o["portrait"], o["landscape"]
    longer = max(p["text_length"], l["text_length"])
    shorter = "portrait" if p["text_length"] < l["text_length"] else "landscape"
    message_in = [name for name, r in o.items() if r["rotate_message"]]
    evidence = json.dumps(o)
    turned = [name for name, r in o.items() if 80 <= r.get("rotation", 0) <= 100]
    if len(message_in) == 1:
        ctx.fail(f"The page asks users to rotate their device in {message_in[0]} orientation.", evidence=evidence)
    elif len(turned) == 1:
        ctx.fail(f"The page turns its content 90 degrees in {turned[0]} orientation, locking it to one orientation.",
                 evidence=evidence)
    elif longer > 200 and min(p["text_length"], l["text_length"]) < longer * 0.4:
        ctx.fail(f"Most content disappears in {shorter} orientation.", evidence=evidence)
    else:
        ctx.passed("Content is available in both portrait and landscape.", evidence=evidence)


@wcag_tool("1.3.5", "check_input_purpose", domain="forms", tech=(AXE, PW), uses=("axe", "forms"), judgement=True)
def check_input_purpose(ctx: Ctx) -> None:
    """For fields collecting the user's own personal data, judge whether a correct autocomplete token is set."""
    fields = form_fields(ctx.data("forms"))
    if not fields:
        ctx.not_applicable("No form fields on the page.")
        return
    ctx.axe()
    missing = [f for f in fields if f["likely_personal_without_autocomplete"]]
    if missing:
        ctx.review(f"{len(missing)} field(s) look like they collect personal data but have no autocomplete token.",
                   [f["selector"] for f in missing], "; ".join(f"{f['label'] or f['name']} ({f['type']})" for f in missing[:6]))
    elif not ctx.findings:
        ctx.passed("Personal-data fields have autocomplete tokens.")


@wcag_tool("1.3.6", "check_component_purpose", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_component_purpose(ctx: Ctx) -> None:
    """Judge whether the purpose of UI components, icons, and regions can be programmatically determined."""
    missing = [k for k, v in ctx.data("page").get("landmarks", {}).items() if not v]
    ctx.review("Check icons, regions, and components expose their purpose (landmarks, ARIA, autocomplete)."
               + (f" Missing landmarks: {', '.join(missing)}." if missing else " All main landmarks present."))


# ---------------------------------------------------------------- 1.4 Distinguishable

@wcag_tool("1.4.1", "check_use_of_color", domain="lead", tech=(AXE, PW), uses=("axe", "page"), judgement=True)
def check_use_of_color(ctx: Ctx) -> None:
    """Judge from the screenshot whether colour is the only way information is conveyed (links, errors, required fields, charts)."""
    ctx.axe()
    axe_failed = any(f.status is Status.FAIL for f in ctx.findings)
    # F73: links in running text with no cue but colour. Under 3:1 against the text is a failure outright; at 3:1 or
    # more the link also needs a cue on hover and focus, which an agent must check.
    low, cue_needed = [], []
    for link in ctx.data("page").get("undecorated_text_links", []):
        fg, tx = parse_color(link["link_color"]), parse_color(link["text_color"])
        ratio = contrast_ratio(fg[:3], tx[:3]) if fg and tx else None
        (low if ratio is not None and ratio < 3 else cue_needed).append({**link, "ratio_to_text": ratio})
    if low and not axe_failed:
        ctx.fail(f"{len(low)} link(s) in text are distinguished from the surrounding text by colour alone, and the "
                 "colour difference is under 3:1.", [l["selector"] for l in low],
                 "; ".join(f"'{l['text']}' {l['link_color']} vs text {l['text_color']} = {l['ratio_to_text']}:1" for l in low[:5]))
    if cue_needed:
        ctx.review(f"{len(cue_needed)} link(s) in running text have no underline or other non-colour cue. Their colour "
                   "differs from the text by at least 3:1, which is only enough if an underline or similar cue appears "
                   "on hover and on focus: inspect_element each link's :hover/:focus styles.",
                   [l["selector"] for l in cue_needed], "; ".join(f"'{l['text']}' ({l['ratio_to_text']}:1)" for l in cue_needed[:5]))
    ctx.review("Check required fields, error states, charts, and the current page or tab don't rely on colour alone.")
    ctx.detail("undecorated_text_links", low + cue_needed)


@wcag_tool("1.4.2", "check_audio_control", domain="media", tech=(PW,), uses=("media",))
def check_audio_control(ctx: Ctx) -> None:
    """Audio that plays automatically for over 3 seconds needs pause/stop or volume control."""
    if _no_media(ctx):
        return
    m = ctx.data("media")
    playing = [x for x in m.get("videos", []) + m.get("audio", []) if (x["autoplay"] or not x["paused"]) and not x["muted"]
               and (x["duration"] is None or x["duration"] > 3)]
    uncontrolled = [x for x in playing if not x["controls"]]
    if uncontrolled:
        ctx.fail(f"{len(uncontrolled)} media element(s) autoplay with sound and have no controls to stop them.",
                 [x["selector"] for x in uncontrolled])
    elif playing:
        ctx.passed("Autoplaying media with sound has controls.")
    elif m.get("videos") or m.get("audio"):
        ctx.passed("No media plays sound automatically.")
    else:
        embedded = (f"{len(m.get('player_iframes', []))} media player frame(s), {m.get('other_iframes', 0)} other frame(s), "
                    f"{m.get('objects', 0)} object/embed, {m.get('canvas', 0)} canvas")
        ctx.review("No <video> or <audio> element, but embedded content could play sound: check nothing plays audio for "
                   "more than 3 seconds without a pause, stop, or volume control.",
                   [p["selector"] for p in m.get("player_iframes", [])], embedded)
    _media_details(ctx, m)


def _text_contrast(ctx: Ctx, level: str) -> None:
    items, unmeasured = ctx.data("contrast").get("text", []), ctx.data("contrast").get("unmeasured_text", [])
    if not items and not unmeasured:
        ctx.not_applicable("No visible text found.")
        return
    failing = [i for i in items if i["ratio"] < text_requirement(i["large_text"], level)]
    computed = [i for i in failing if i["method"] == "computed"]
    sampled = [i for i in failing if i["method"] != "computed"]

    def show(rows):
        return "; ".join(f"'{r['text'][:30]}' {r['ratio']}:1 (needs {text_requirement(r['large_text'], level)}:1, "
                         f"{r['foreground']} on {r['background']})" for r in rows[:6])

    if computed:
        ctx.fail(f"{len(computed)} text element(s) are below the {level} contrast ratio.", [r["selector"] for r in computed], show(computed))
    if sampled:
        ctx.review(f"{len(sampled)} text element(s) over images/gradients measured below {level} by pixel sampling; take a "
                   "screenshot and inspect_element to judge the text against its real background.",
                   [r["selector"] for r in sampled], show(sampled))
    if unmeasured:
        ctx.review(f"{len(unmeasured)} text element(s) over images or gradients couldn't be measured; take a screenshot and "
                   "judge them.", [u["selector"] for u in unmeasured], "; ".join(f"'{u['text'][:30]}': {u['why']}"
                                                                         for u in unmeasured[:4]))
    if items and not failing:
        ctx.passed(f"All {len(items)} text elements meet the {level} contrast ratio (lowest {min(i['ratio'] for i in items)}:1).")
    ctx.detail("lowest_ratios", sorted(({"text": i["text"][:30], "ratio": i["ratio"], "method": i["method"]} for i in items),
                                       key=lambda x: x["ratio"]))


@wcag_tool("1.4.3", "check_minimum_text_contrast", domain="lead", tech=(CCA, AXE), uses=("contrast", "axe"))
def check_minimum_text_contrast(ctx: Ctx) -> None:
    """Text needs 4.5:1 (3:1 for large text)."""
    _text_contrast(ctx, "AA")
    ctx.axe()  # axe-core's colour-contrast results are a second, independent measurement


@wcag_tool("1.4.4", "check_resize_text", domain="lead", tech=(PW,), uses=("zoom",))
def check_resize_text(ctx: Ctx) -> None:
    """Text must resize to 200% without loss of content or function."""
    z = ctx.data("zoom")
    if not z:
        return
    unscaled = [f for f in z["font_sizes"] if f["after_px"] < f["before_px"] * MIN_SCALE_RATIO]
    if z["newly_clipped"]:
        ctx.fail(f"At 200% zoom, {len(z['newly_clipped'])} element(s) cut off their text.",
                 [c["selector"] for c in z["newly_clipped"]], "; ".join(f"'{c['text']}'" for c in z["newly_clipped"][:5]))
    if z["lost_off_screen"]:
        ctx.fail(f"At 200% zoom, {len(z['lost_off_screen'])} text element(s) move off-screen where the page can't scroll.",
                 [c["selector"] for c in z["lost_off_screen"]])
    if unscaled:
        ctx.fail(f"{len(unscaled)} text element(s) don't grow with zoom (font size tied to the viewport).",
                 [u["selector"] for u in unscaled],
                 "; ".join(f"'{u['text']}' {u['before_px']}px -> {u['after_px']}px CSS" for u in unscaled[:5]))
    if not ctx.findings:
        ctx.passed("At 200% zoom no text was clipped, lost off-screen, or failed to scale.")


def _images_of_text(ctx: Ctx, strict: bool) -> None:
    page = ctx.data("page")
    images = [i for i in page.get("images", []) if not i["hidden"] and i["tag"] in ("img", "input", "area")]
    if not images and not page.get("background_images"):
        ctx.not_applicable("No images on the page.")
        return
    wordy = [i for i in images if i["alt"] and len(i["alt"].split()) >= 3 and i["width"] > 60]
    named = [i for i in images if i not in wordy and TEXT_IMAGE_SRC.search(i["src"] or "")]
    suspects = wordy + named
    msg = "Check no image presents text that could be real text" + ("" if strict else " (logos and essential text are exempt)") + "."
    hints = ([f" {len(wordy)} image(s) have sentence-like alt text, which often means text in the image." if wordy else ""]
             + [f" {len(named)} image(s) have file names that suggest text (title, banner, slogan...)." if named else ""])
    ctx.review(msg + "".join(hints) + " take_screenshot and look at each listed image.",
               [i["selector"] for i in suspects] or [i["selector"] for i in images][:8],
               "; ".join(f"{i['src']} alt='{(i['alt'] or '')[:50]}'" for i in suspects[:5]) or None)
    ctx.detail("images", [{"selector": i["selector"], "src": i["src"], "alt": i["alt"], "width": i["width"]} for i in images])


@wcag_tool("1.4.5", "check_images_of_text", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_images_of_text(ctx: Ctx) -> None:
    """Judge from the screenshot whether any image shows text that could be real text (logos exempt)."""
    _images_of_text(ctx, strict=False)


@wcag_tool("1.4.6", "check_enhanced_text_contrast", domain="lead", tech=(CCA,), uses=("contrast",))
def check_enhanced_text_contrast(ctx: Ctx) -> None:
    """Text needs 7:1 (4.5:1 for large text)."""
    _text_contrast(ctx, "AAA")


@wcag_tool("1.4.7", "check_background_audio", domain="media", tech=(PW,), uses=("media",), judgement=True)
def check_background_audio(ctx: Ctx) -> None:
    """For prerecorded speech audio, check background sound is off, can be turned off, or is 20 dB quieter."""
    _media_review(ctx, "Check background sound in speech audio can be turned off or is 20 dB below speech.", need_video=False)


@wcag_tool("1.4.8", "check_visual_presentation", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_visual_presentation(ctx: Ctx) -> None:
    """Check users can choose colours; lines are <=80 characters; text isn't justified; line spacing >=1.5; resizable to 200%."""
    paras = ctx.data("page").get("paragraphs", [])
    if not paras:
        ctx.not_applicable("No blocks of text long enough to assess.")
        return
    justified = [p for p in paras if p["text_align"] == "justify"]
    long_lines = [p for p in paras if p["chars_per_line"] > 80]
    tight = [p for p in paras if p["line_height"] < 1.5]
    if justified:
        ctx.fail(f"{len(justified)} block(s) of text are justified.", [p["selector"] for p in justified])
    if long_lines:
        # 1.4.8 asks for a mechanism to get lines to 80 characters; text that reflows as the window narrows is one (H87).
        ctx.review(f"{len(long_lines)} block(s) have lines over 80 characters at this width (about "
                   f"{max(p['chars_per_line'] for p in long_lines)}); check narrowing the window or another mechanism "
                   "brings them to 80.", [p["selector"] for p in long_lines])
    if tight:
        ctx.review(f"{len(tight)} block(s) have line spacing under 1.5; check users can change it.", [p["selector"] for p in tight])
    ctx.review("Check users can select foreground and background colours (e.g. via browser settings).")


@wcag_tool("1.4.9", "check_images_of_text_no_exception", domain="lead", tech=(PW,), uses=("page",), judgement=True)
def check_images_of_text_no_exception(ctx: Ctx) -> None:
    """Judge whether any image of text exists that isn't pure decoration or essential (e.g. a logo)."""
    _images_of_text(ctx, strict=True)


@wcag_tool("1.4.10", "check_reflow", domain="lead", tech=(PW,), uses=("reflow",))
def check_reflow(ctx: Ctx) -> None:
    """No horizontal scrolling at 320 CSS px width (except content that needs 2-D layout)."""
    r = ctx.data("reflow")
    if not r:
        return
    if clipped := r.get("clipped"):
        ctx.review(f"At 320px wide, {len(clipped)} text element(s) are cut off by a container that hides its overflow; "
                   "check the text is still available another way.", [c["selector"] for c in clipped],
                   "; ".join(f"'{c['text']}'" for c in clipped[:5]))
    if not r["horizontal_scroll"]:
        if not clipped:
            ctx.passed("No horizontal scrolling at 320 CSS px width, and no text cut off by a container.")
        return
    real = [o for o in r["offenders"] if not o["likely_exception"]]
    culprits = real or r["offenders"]
    ctx.add(Status.FAIL if real or not r["offenders"] else Status.NEEDS_REVIEW,
            f"Page scrolls horizontally at 320px wide (content {r['scroll_width']}px)."
            + ("" if real else " The overflowing content may be an allowed exception (data table, image, map, code): "
               "inspect_element and a screenshot settle it."),
            [o["selector"] for o in culprits], "; ".join(f"{o['selector']} is {o['width']}px wide" for o in culprits[:5]) or None)


@wcag_tool("1.4.11", "check_non_text_contrast", domain="lead", tech=(CCA, PW), uses=("contrast",), judgement=True)
def check_non_text_contrast(ctx: Ctx) -> None:
    """Control boundaries, icons, and meaningful graphics need 3:1; judge charts and graphics from the screenshot."""
    items = ctx.data("contrast").get("non_text", [])
    low = [i for i in items if i["ratio"] is not None and i["ratio"] < 3]
    unknown = [i for i in items if i["ratio"] is None]
    if low:
        ctx.fail(f"{len(low)} control boundary/icon(s) are below 3:1 against their background.", [i["selector"] for i in low],
                 "; ".join(f"{i['kind']} '{i['name'][:25]}' {i['ratio']}:1" for i in low[:6]))
    if unknown:
        ctx.review(f"{len(unknown)} component(s) sit on an image/gradient; check 3:1 visually.", [i["selector"] for i in unknown])
    if items and not low:
        ctx.passed(f"{len(items) - len(unknown)} control boundaries/icons meet 3:1.")
    ctx.review("Check charts, graphs, and other meaningful graphics reach 3:1 for the parts needed to understand them.")


@wcag_tool("1.4.12", "check_text_spacing", domain="lead", tech=(PW,), uses=("text_spacing",))
def check_text_spacing(ctx: Ctx) -> None:
    """No loss of content when line, paragraph, letter, and word spacing are increased."""
    clipped = ctx.data("text_spacing").get("newly_clipped")
    if clipped is None:
        return
    if clipped:
        ctx.fail(f"{len(clipped)} element(s) cut off text when WCAG text spacing is applied.",
                 [c["selector"] for c in clipped], "; ".join(f"'{c['text']}'" for c in clipped[:5]))
    else:
        ctx.passed("No text became clipped with increased line, letter, word, and paragraph spacing.")


@wcag_tool("1.4.13", "check_content_on_hover_or_focus", domain="navigation", tech=(PW,), uses=("hover",))
def check_content_on_hover_or_focus(ctx: Ctx) -> None:
    """Pop-ups on hover/focus must be dismissible, hoverable, and persistent."""
    h = ctx.data("hover")
    if not h:
        return
    problems = []
    for r in h["hover"]:
        issues = [why for bad, why in ((not r["hoverable"], "disappears when the pointer moves onto it"),
                                       (r["persistent"] is False, "disappears on its own"),
                                       # Dismissal is only required when the content covers other content.
                                       (not r["dismissible"] and r["obscures"], "covers other content and Escape doesn't dismiss it"))
                  if bad]
        if issues:
            problems.append((r, "on hover: " + ", ".join(issues)))
    problems += [(r, "on focus: covers other content and Escape doesn't dismiss it")
                 for r in h["focus"] if not r["dismissible"] and r["obscures"]]
    if problems:
        ctx.fail(f"{len(problems)} pop-up(s) shown on hover or focus can't be hovered, kept, or dismissed.",
                 [p["trigger"] for p, _ in problems], "; ".join(f"{p['trigger']} ('{p['content'][:40]}') {why}" for p, why in problems[:5]))
    elif h["hover"] or h["focus"]:
        ctx.passed(f"{len(h['hover']) + len(h['focus'])} pop-up(s) are hoverable, persistent, and dismissible.")
    else:
        ctx.passed(f"None of the {h['triggers_tested']} likely triggers showed extra content on hover or focus.")
