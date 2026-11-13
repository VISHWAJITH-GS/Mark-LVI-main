"""
action_executor.py — Root re-export of actions.action_executor for direct import compatibility.
"""
from actions.action_executor import (
    ActionExecutor,
    get_action_executor,
    tool_click_element,
    tool_input_text,
    tool_scroll_page,
    tool_scroll_element,
    tool_press_key,
    tool_navigate_to,
    tool_switch_tab,
    tool_go_back,
    tool_wait,
    tool_observe_page,
    TOOLS,
)

__all__ = [
    "ActionExecutor",
    "get_action_executor",
    "tool_click_element",
    "tool_input_text",
    "tool_scroll_page",
    "tool_scroll_element",
    "tool_press_key",
    "tool_navigate_to",
    "tool_switch_tab",
    "tool_go_back",
    "tool_wait",
    "tool_observe_page",
    "TOOLS",
]

