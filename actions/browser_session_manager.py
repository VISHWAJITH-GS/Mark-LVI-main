"""
actions/browser_session_manager.py — Compatibility bridge redirecting to actions.browser_session.

Deprecated legacy isolated test profile launcher in favor of live Edge CDP session attachment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Optional

from actions.browser_session import (
    BrowserSessionManager,
    BrowserSessionState,
    CDPConnectionError,
    FrameInfo,
    TabInfo,
    get_browser_session_manager,
    DEFAULT_CDP_URL,
)


@dataclass
class BrowserNetworkError:
    url: str
    method: str
    status: Optional[int]
    error_text: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class BrowserConsoleMessage:
    msg_type: str
    text: str
    location: str
    timestamp: float = field(default_factory=time.time)


__all__ = [
    "BrowserSessionManager",
    "BrowserSessionState",
    "CDPConnectionError",
    "FrameInfo",
    "TabInfo",
    "get_browser_session_manager",
    "DEFAULT_CDP_URL",
    "BrowserNetworkError",
    "BrowserConsoleMessage",
]
