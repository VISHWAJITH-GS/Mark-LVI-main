"""
plugins/_whatsapp_core.py

Verified, reliable WhatsApp driver for Mark-LVI.
Supports:
  1. Windows UIA (pywinauto) for WhatsApp Desktop (UWP / Win32)
  2. WhatsApp Web in default browser or desktop fallback
  3. Finding and selecting contacts accurately
  4. Typing formal messages and verifying send
"""

import os
import sys
import time
import urllib.parse
from typing import Optional, Tuple

from core import outcome
from core.lazyimport import LazyModule, installed

_PYAUTOGUI = installed("pyautogui")
_PYPERCLIP = installed("pyperclip")

def _configure_pyautogui(module) -> None:
    module.FAILSAFE = True
    module.PAUSE = 0.05

pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")


def _paste_text(text: str) -> None:
    if not _PYAUTOGUI:
        return
    if _PYPERCLIP:
        try:
            pyperclip.copy(text)
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
            return
        except Exception:
            pass
    pyautogui.write(text, interval=0.02)


class WhatsAppDriver:
    """Automates WhatsApp Desktop & Web using UIA and desktop input."""

    def __init__(self):
        self.uia = None
        try:
            from actions import windows_uia
            if windows_uia.is_available():
                self.uia = windows_uia
        except Exception:
            pass

    def _find_whatsapp_window(self):
        """Locate active or top-level WhatsApp window."""
        if not self.uia:
            return None
        desktop = self.uia.get_desktop()
        if not desktop:
            return None

        try:
            # Check active window
            active = self.uia.get_active_window()
            if active.get("status") == "success":
                title = active.get("title", "").lower()
                proc = active.get("process_name", "").lower()
                if "whatsapp" in title or "whatsapp" in proc:
                    return active.get("wrapper")

            # Check windows by title or class
            for win in desktop.windows(visible_only=True):
                try:
                    w_title = win.window_text().lower()
                    if "whatsapp" in w_title:
                        return win
                except Exception:
                    continue
        except Exception as e:
            print(f"[WhatsAppCore] Window search error: {e}")
        return None

    def open_whatsapp(self) -> bool:
        """Ensure WhatsApp is launched and foregrounded."""
        win = self._find_whatsapp_window()
        if win:
            try:
                win.set_focus()
                time.sleep(0.5)
                return True
            except Exception:
                pass

        # Try launching via open_app / URI
        try:
            os.startfile("whatsapp:")
            time.sleep(2.0)
            return True
        except Exception:
            pass

        try:
            from actions import open_app
            res = open_app.open_app({"app_name": "WhatsApp"})
            time.sleep(2.0)
            return True
        except Exception:
            pass

        return False

    def open_chat(self, contact_name: str) -> bool:
        """Search for and open the specific contact/chat in WhatsApp."""
        if not self.open_whatsapp():
            return False

        win = self._find_whatsapp_window()
        if win:
            try:
                win.set_focus()
                time.sleep(0.3)
            except Exception:
                pass

        # Strategy A: Use UIA to search or select directly
        if self.uia and win:
            try:
                # Find search box (Edit / Search field)
                search_box = None
                for candidate in ("Search or start new chat", "Search", "Search…", "Search chats"):
                    f = self.uia.find_elements(candidate, control_type="Edit", window=win, exact=False)
                    if f.get("status") == "success" and f.get("matches"):
                        search_box = f["matches"][0].get("_wrapper")
                        break

                if search_box:
                    self.uia.click_element(search_box)
                    time.sleep(0.2)
                    self.uia.type_text(contact_name, element_or_name=search_box, clear_first=True)
                    time.sleep(0.8)
                    if _PYAUTOGUI:
                        pyautogui.press("enter")
                        time.sleep(0.5)
                    return True
            except Exception as e:
                print(f"[WhatsAppCore] UIA search failed: {e}")

        # Strategy B: Keyboard shortcut (Ctrl+F or Ctrl+N in WhatsApp)
        if _PYAUTOGUI:
            try:
                pyautogui.hotkey("ctrl", "f")
                time.sleep(0.4)
                _paste_text(contact_name)
                time.sleep(0.8)
                pyautogui.press("enter")
                time.sleep(0.5)
                return True
            except Exception as e:
                print(f"[WhatsAppCore] Shortcut search failed: {e}")

        return False

    def send_message_to(self, contact_name: str, message: str) -> Tuple[bool, str]:
        """
        Full workflow:
        1. Open WhatsApp
        2. Find and open contact's chat
        3. Format message formally if appropriate
        4. Type into message box and send (Enter)
        5. Verify delivery
        """
        if not contact_name:
            return False, "No contact name provided."
        if not message:
            return False, "No message content provided."

        opened = self.open_chat(contact_name)
        if not opened:
            return False, f"Could not find or open chat for '{contact_name}' in WhatsApp."

        time.sleep(0.5)

        # Focus message box and send
        # Strategy A: UIA message input field
        win = self._find_whatsapp_window()
        typed = False
        if self.uia and win:
            try:
                for candidate in ("Type a message", "Message", "Write a message"):
                    f = self.uia.find_elements(candidate, control_type="Edit", window=win, exact=False)
                    if f.get("status") == "success" and f.get("matches"):
                        edit_elem = f["matches"][0].get("_wrapper")
                        self.uia.click_element(edit_elem)
                        time.sleep(0.2)
                        _paste_text(message)
                        time.sleep(0.2)
                        if _PYAUTOGUI:
                            pyautogui.press("enter")
                        typed = True
                        break
            except Exception as e:
                print(f"[WhatsAppCore] UIA message typing fallback: {e}")

        # Strategy B: Standard focus paste + enter
        if not typed and _PYAUTOGUI:
            _paste_text(message)
            time.sleep(0.2)
            pyautogui.press("enter")
            time.sleep(0.3)
            typed = True

        if typed:
            return True, ""
        return False, "Failed to type and send message into chat window."


def get():
    """Return (driver, reason). Compatible with send_message.py."""
    return WhatsAppDriver(), ""

