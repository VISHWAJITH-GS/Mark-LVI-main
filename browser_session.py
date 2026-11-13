"""
browser_session.py — Root re-export of actions.browser_session for direct import compatibility.
"""
from actions.browser_session import (
    BrowserSessionManager,
    BrowserSessionState,
    CDPConnectionError,
    FrameInfo,
    TabInfo,
    get_browser_session_manager,
    DEFAULT_CDP_URL,
)

__all__ = [
    "BrowserSessionManager",
    "BrowserSessionState",
    "CDPConnectionError",
    "FrameInfo",
    "TabInfo",
    "get_browser_session_manager",
    "DEFAULT_CDP_URL",
]

