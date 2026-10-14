"""The function tools agents call: page inspection and interaction (browser_tools), findings and reports
(report_tools), and the planning loop (planning). Which agent gets which tools is decided in agent_access/team.
Criterion and re-test tools live in agent_access/engine.py.
"""

from agent_access.agent_tools.browser_tools import (
    click_element,
    fill_field,
    get_accessibility_tree,
    get_page_outline,
    inspect_element,
    label_page_state,
    press_key,
    take_screenshot,
)
from agent_access.agent_tools.report_tools import get_recorded_findings

# Read-only inspection, shared by most agents.
INSPECTION_TOOLS = [get_page_outline, get_accessibility_tree, inspect_element, take_screenshot, get_recorded_findings]

# Free-form interaction for multi-step processes (only enabled when AuditContext.allow_interaction is true).
INTERACTION_TOOLS = [click_element, fill_field, press_key, label_page_state]
