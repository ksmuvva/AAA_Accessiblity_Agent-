"""The 86 criterion tools: registry, exact names, and expected results on fixtures with known defects."""

import asyncio
from pathlib import Path

import pytest

from agent_access.catalogue.catalogue import load_catalogue
from agent_access.agent_tools.browser_tools import load_html_content
from agent_access.probes.contrast import contrast_ratio, is_large_text, text_requirement
from agent_access.context import AuditContext
from agent_access.models import Status
from agent_access.probes.screen_reader import spoken_name, spoken_role
from agent_access.engine import run_sweep
from agent_access.rules import REGISTRY, TOOLS_IN_ORDER
from agent_access.rules.understandable import _script, flesch_kincaid_grade

FIXTURES = Path(__file__).parent / "fixtures"

# The tool names the project owner specified, criterion by criterion.
EXPECTED_NAMES = {
    "1.1.1": "check_non_text_content", "1.2.1": "check_prerecorded_audio_video_alternatives",
    "1.2.2": "check_prerecorded_captions", "1.2.3": "check_audio_description_or_media_alternative",
    "1.2.4": "check_live_captions", "1.2.5": "check_prerecorded_audio_description",
    "1.2.6": "check_prerecorded_sign_language", "1.2.7": "check_extended_audio_description",
    "1.2.8": "check_prerecorded_media_alternative", "1.2.9": "check_live_audio_alternative",
    "1.3.1": "check_info_and_relationships", "1.3.2": "check_meaningful_sequence",
    "1.3.3": "check_sensory_characteristics", "1.3.4": "check_orientation", "1.3.5": "check_input_purpose",
    "1.3.6": "check_component_purpose", "1.4.1": "check_use_of_color", "1.4.2": "check_audio_control",
    "1.4.3": "check_minimum_text_contrast", "1.4.4": "check_resize_text", "1.4.5": "check_images_of_text",
    "1.4.6": "check_enhanced_text_contrast", "1.4.7": "check_background_audio", "1.4.8": "check_visual_presentation",
    "1.4.9": "check_images_of_text_no_exception", "1.4.10": "check_reflow", "1.4.11": "check_non_text_contrast",
    "1.4.12": "check_text_spacing", "1.4.13": "check_content_on_hover_or_focus",
    "2.1.1": "check_keyboard_accessibility", "2.1.2": "check_keyboard_traps",
    "2.1.3": "check_keyboard_accessibility_no_exception", "2.1.4": "check_character_key_shortcuts",
    "2.2.1": "check_timing_adjustable", "2.2.2": "check_pause_stop_hide", "2.2.3": "check_no_timing",
    "2.2.4": "check_interruptions", "2.2.5": "check_reauthentication_data_preservation",
    "2.2.6": "check_timeout_warnings", "2.3.1": "check_flash_thresholds", "2.3.2": "check_three_flashes",
    "2.3.3": "check_animation_from_interactions", "2.4.1": "check_bypass_blocks", "2.4.2": "check_page_title",
    "2.4.3": "check_focus_order", "2.4.4": "check_link_purpose_in_context", "2.4.5": "check_multiple_navigation_ways",
    "2.4.6": "check_headings_and_labels", "2.4.7": "check_focus_visible", "2.4.8": "check_location_information",
    "2.4.9": "check_link_purpose_link_only", "2.4.10": "check_section_headings",
    "2.4.11": "check_focus_not_obscured_minimum", "2.4.12": "check_focus_not_obscured_enhanced",
    "2.4.13": "check_focus_appearance", "2.5.1": "check_pointer_gestures", "2.5.2": "check_pointer_cancellation",
    "2.5.3": "check_label_in_name", "2.5.4": "check_motion_actuation", "2.5.5": "check_target_size_enhanced",
    "2.5.6": "check_concurrent_input_mechanisms", "2.5.7": "check_dragging_alternatives",
    "2.5.8": "check_target_size_minimum", "3.1.1": "check_page_language", "3.1.2": "check_language_of_parts",
    "3.1.3": "check_unusual_words", "3.1.4": "check_abbreviations", "3.1.5": "check_reading_level",
    "3.1.6": "check_pronunciation_support", "3.2.1": "check_context_changes_on_focus",
    "3.2.2": "check_context_changes_on_input", "3.2.3": "check_consistent_navigation",
    "3.2.4": "check_consistent_identification", "3.2.5": "check_change_on_request", "3.2.6": "check_consistent_help",
    "3.3.1": "check_error_identification", "3.3.2": "check_labels_or_instructions", "3.3.3": "check_error_suggestions",
    "3.3.4": "check_error_prevention_legal_financial_data", "3.3.5": "check_context_sensitive_help",
    "3.3.6": "check_error_prevention_all", "3.3.7": "check_redundant_entry",
    "3.3.8": "check_accessible_authentication_minimum", "3.3.9": "check_accessible_authentication_enhanced",
    "4.1.2": "check_name_role_value", "4.1.3": "check_status_messages",
}


def test_one_tool_per_criterion_with_the_specified_names():
    assert {c: t.name for c, t in REGISTRY.items()} == EXPECTED_NAMES
    assert set(REGISTRY) == {r.wcag_criterion for r in load_catalogue().rules}


def test_contrast_math_matches_wcag():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == 21.0
    assert contrast_ratio((118, 118, 118), (255, 255, 255)) == 4.54  # #767676 is the classic 4.5:1 grey
    assert is_large_text(24, 400) and is_large_text(18.67, 700) and not is_large_text(18, 700)
    assert text_requirement(False, "AA") == 4.5 and text_requirement(True, "AAA") == 4.5


def test_script_detection_and_reading_level():
    assert _script("Данный домен предназначен для использования") == "Cyrillic"
    assert _script("This domain is for use in documentation") == "Latin"
    assert _script("该域名仅用于文档示例无需获得许可") == "Han"
    grade, words = flesch_kincaid_grade("The cat sat on the mat. It was a sunny day. " * 20)
    assert words == 220 and grade < 3


def test_announcement_parsing():
    assert spoken_name("Send message, button, focused") == "Send message"
    assert spoken_role("Send message, button, focused") == "button"
    assert spoken_name("edit, focused") == "" and spoken_role("edit, focused") == "edit"
    assert spoken_role("Clickable span, section, focused") is None


def sweep(name: str):
    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, (FIXTURES / name).read_text(encoding="utf-8"), name)
            await run_sweep(audit)
            return audit

    audit = asyncio.run(scenario())
    by_tool: dict[str, set[Status]] = {}
    for f in audit.findings:
        by_tool.setdefault(f.check_id, set()).add(f.status)
    return audit, by_tool


@pytest.fixture(scope="module")
def bad():
    return sweep("bad_page.html")


@pytest.fixture(scope="module")
def custom_fail():
    return sweep("custom_fail_page.html")


@pytest.mark.parametrize("tool", [
    "check_non_text_content", "check_minimum_text_contrast", "check_page_title", "check_page_language",
    "check_labels_or_instructions", "check_name_role_value",
])
def test_bad_page_failures(bad, tool):
    assert Status.FAIL in bad[1][tool]


@pytest.mark.parametrize("tool", [
    "check_reflow", "check_text_spacing", "check_focus_visible", "check_focus_not_obscured_minimum",
    "check_focus_not_obscured_enhanced", "check_focus_appearance", "check_target_size_minimum", "check_target_size_enhanced",
])
def test_custom_fail_page_failures(custom_fail, tool):
    assert Status.FAIL in custom_fail[1][tool]


def test_no_media_means_not_applicable(bad):
    for tool in ("check_prerecorded_captions", "check_prerecorded_sign_language", "check_live_captions",
                 "check_background_audio", "check_audio_control"):
        assert bad[1][tool] == {Status.NOT_APPLICABLE}, tool


def test_clean_page_has_few_failures():
    _, by_tool = sweep("good_page.html")
    fails = sorted(t for t, s in by_tool.items() if Status.FAIL in s)
    # Only AAA-level strictness may fail on an otherwise good page.
    level = {t.name: t.level for t in TOOLS_IN_ORDER}
    assert all(level[t] == "AAA" for t in fails), fails


def test_language_of_parts_catches_wrong_script():
    html = ('<html lang="en"><head><title>Docs</title></head><body><main><h1>Docs</h1>'
            '<p>This domain is for use in documentation examples.</p>'
            '<p>Данный домен предназначен для использования в примерах документации.</p>'
            '<p lang="ru">Этот абзац правильно помечен как русский текст для чтения.</p></main></body></html>')

    async def scenario():
        async with AuditContext(screen_reader="emulated") as audit:
            await load_html_content(audit, html)
            await run_sweep(audit)
            return [f for f in audit.findings if f.check_id == "check_language_of_parts" and f.status is Status.FAIL]

    fails = asyncio.run(scenario())
    assert len(fails) == 1 and len(fails[0].selectors) == 1  # only the unmarked Russian paragraph
