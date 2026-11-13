"""
actions/notification_reader.py — Windows Notification Center Reader and Interpreter.

Capabilities:
- Retrieve accessible Windows notifications (WinRT UserNotificationListener + UIA Action Center fallback)
- Identify originating application, title, sender, and content
- Read recent notifications aloud
- Summarize multiple notifications via Gemini
- Filter notifications by application (e.g. "WhatsApp", "Outlook")
- Handle permission restrictions and unavailable content gracefully
- Track read notification IDs to prevent repetitive reading
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core import gemini, outcome

logger = logging.getLogger(__name__)
_IS_WINDOWS = platform.system() == "Windows"
_WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


@dataclass
class DesktopNotification:
    id: str
    app_name: str
    title: str
    body: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%I:%M %p"))
    read: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "app_name": self.app_name,
            "title": self.title,
            "body": self.body,
            "timestamp": self.timestamp,
        }

    def format_speech(self) -> str:
        if self.title and self.body:
            return f"From {self.app_name}: {self.title} — {self.body}"
        elif self.title:
            return f"From {self.app_name}: {self.title}"
        elif self.body:
            return f"From {self.app_name}: {self.body}"
        return f"Notification from {self.app_name} with hidden content."


class NotificationReader:
    """Multi-tiered Windows notification reader."""

    def __init__(self):
        self._read_hashes: set[str] = set()

    def _run_ps(self, script: str, timeout: int = 10) -> tuple[int, str, str]:
        if not _IS_WINDOWS:
            return 1, "", "Non-Windows system."
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                **_WIN_HIDE,
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except Exception as e:
            return 1, "", str(e)

    # ── Tier 1: WinRT UserNotificationListener ──────────────────────────────
    def _query_winrt_notifications(self) -> Tuple[bool, List[DesktopNotification], str]:
        ps = """
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.UI.Notifications.Management.UserNotificationListener, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$listener = [Windows.UI.Notifications.Management.UserNotificationListener]::Current

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

$accOp = $listener.RequestAccessAsync()
$accTask = $asTaskGeneric.MakeGenericMethod([Windows.UI.Notifications.Management.UserNotificationListenerAccessStatus]).Invoke($null, @($accOp))
$accTask.Wait(2500)
$status = $accTask.Result.ToString()

if ($status -ne "Allowed") {
    Write-Output "ACCESS_DENIED: $status"
    exit 0
}

$op = $listener.GetNotificationsAsync([Windows.UI.Notifications.NotificationKinds]::Toast)
$task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.UI.Notifications.UserNotification]]).Invoke($null, @($op))
$task.Wait(3000)

$results = @()
foreach ($n in $task.Result) {
    $app = $n.AppInfo.DisplayInfo.DisplayName
    $binding = $n.Notification.Visual.GetBinding([Windows.UI.Notifications.KnownNotificationBindings]::ToastGeneric)
    $title = ""
    $body = ""
    if ($binding) {
        $texts = $binding.GetTextElements()
        if ($texts.Count -gt 0) { $title = $texts[0].Text }
        if ($texts.Count -gt 1) { $body = $texts[1].Text }
    }
    $results += [PSCustomObject]@{
        Id = $n.Id
        App = $app
        Title = $title
        Body = $body
    }
}
$results | ConvertTo-Json -Depth 2
"""
        code, stdout, stderr = self._run_ps(ps, timeout=12)
        if "ACCESS_DENIED" in stdout:
            return False, [], "Windows Notification Access is restricted or denied in Windows Settings."

        if not stdout or not stdout.strip():
            return True, [], "No active notifications found in Windows Action Center."

        if code != 0:
            return False, [], stderr or "Error querying notifications."

        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                data = [data]
            notifs = []
            for item in data:
                app = item.get("App") or "System"
                title = item.get("Title") or ""
                body = item.get("Body") or ""
                nid = str(item.get("Id") or hashlib.md5(f"{app}{title}{body}".encode()).hexdigest()[:8])
                notifs.append(DesktopNotification(id=nid, app_name=app, title=title, body=body))
            return True, notifs, ""
        except Exception as e:
            return False, [], f"JSON parse error: {e}"

    # ── Tier 2: UIA Inspection of Notification Center / Toast Banners ────────
    def _query_uia_notifications(self) -> List[DesktopNotification]:
        notifs: List[DesktopNotification] = []
        try:
            from actions import windows_uia
            if not windows_uia.is_available():
                return notifs

            desktop = windows_uia.get_desktop()
            if not desktop:
                return notifs

            # Look for active toast banners or open Notification Center
            for win in desktop.windows(visible_only=True):
                try:
                    w_title = win.window_text()
                    c_name = win.class_name()
                    if "notification" in w_title.lower() or "action center" in w_title.lower() or "toast" in c_name.lower():
                        # Scrape text elements
                        elems = win.descendants(control_type="Text")
                        texts = [e.window_text().strip() for e in elems if e.window_text().strip()]
                        if len(texts) >= 2:
                            app = texts[0]
                            body = " — ".join(texts[1:3])
                            nid = hashlib.md5(f"{app}{body}".encode()).hexdigest()[:8]
                            notifs.append(DesktopNotification(id=nid, app_name=app, title=texts[1], body=texts[2] if len(texts) > 2 else ""))
                except Exception:
                    continue
        except Exception:
            pass
        return notifs

    # ── Public Notification Retrieval ────────────────────────────────────────
    def get_notifications(
        self,
        app_filter: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 8,
    ) -> outcome.ActionResult:
        """Fetch accessible Windows notifications with app filtering."""
        t0 = time.monotonic()
        ok, notifs, reason = self._query_winrt_notifications()

        if not ok:
            # Fallback to UIA
            notifs = self._query_uia_notifications()

        # Apply app filter if requested (e.g. "WhatsApp", "Slack", "Chrome")
        if app_filter:
            flt = app_filter.lower().strip()
            notifs = [n for n in notifs if flt in n.app_name.lower() or flt in n.title.lower()]

        if unread_only:
            notifs = [n for n in notifs if n.id not in self._read_hashes]

        # Record hashes as read
        for n in notifs:
            self._read_hashes.add(n.id)

        count = len(notifs)
        if count == 0:
            filter_note = f" from {app_filter}" if app_filter else ""
            msg = f"No new notifications{filter_note}." if not reason else f"No accessible notifications{filter_note} ({reason})."
            return outcome.ActionResult(
                status=outcome.STATUS_DONE if ok else outcome.STATUS_UNVERIFIED,
                action="get_notifications",
                success=True,
                verified=True,
                message=msg,
                data={"count": 0, "notifications": [], "reason": reason},
                evidence=f"Checked Windows notification listener (Access: {'Allowed' if ok else 'Fallback'}).",
            )

        speech_lines = [n.format_speech() for n in notifs[:limit]]
        summary_text = f"You have {count} notification{'s' if count != 1 else ''}:\n" + "\n".join([f"• {s}" for s in speech_lines])

        return outcome.ActionResult(
            status=outcome.STATUS_DONE,
            action="get_notifications",
            success=True,
            verified=True,
            message=summary_text,
            data={"count": count, "notifications": [n.to_dict() for n in notifs[:limit]]},
            evidence=f"Retrieved {count} notifications from Windows Notification Center.",
            duration_ms=(time.monotonic() - t0) * 1000,
        )

    def summarize_notifications(self, app_filter: Optional[str] = None) -> outcome.ActionResult:
        """Fetch and generate a concise Gemini natural-language summary of notifications."""
        res = self.get_notifications(app_filter=app_filter, limit=10)
        notifs_data = res.data.get("notifications", [])

        if not notifs_data:
            return res

        raw_text = "\n".join([f"{n['app_name']}: {n['title']} - {n['body']}" for n in notifs_data])
        prompt = (
            "Summarize the following Windows desktop notifications in 2 polite, concise sentences. "
            "Highlight the most important messages and who they are from:\n\n"
            f"{raw_text}"
        )
        try:
            summary = gemini.text(prompt, tier=gemini.FAST, timeout_ms=5000)
            if summary and summary.strip():
                return outcome.ActionResult(
                    status=outcome.STATUS_DONE,
                    action="summarize_notifications",
                    success=True,
                    verified=True,
                    message=summary.strip(),
                    data={"count": len(notifs_data), "summary": summary.strip()},
                    evidence="Summarized notifications using Gemini.",
                )
        except Exception:
            pass

        return res


_DEFAULT_READER: Optional[NotificationReader] = None


def get_notification_reader() -> NotificationReader:
    global _DEFAULT_READER
    if _DEFAULT_READER is None:
        _DEFAULT_READER = NotificationReader()
    return _DEFAULT_READER


def notification_center(parameters: dict = None, player=None, **kwargs) -> str:
    """Auto-discovered tool handler for Windows Notification Center."""
    params = dict(parameters or {})
    params.update(kwargs)

    action = (params.get("action") or "read").lower().strip()
    app_filter = params.get("app") or params.get("application") or params.get("sender")
    unread_only = bool(params.get("unread_only", False))
    limit = int(params.get("limit") or 6)

    reader = get_notification_reader()

    if action in ("summarize", "summary", "summarize_notifications"):
        res = reader.summarize_notifications(app_filter=app_filter)
    else:
        res = reader.get_notifications(app_filter=app_filter, unread_only=unread_only, limit=limit)

    if player and hasattr(player, "write_log"):
        player.write_log(f"[Notifications] 🔔 {res.message[:60]}…")

    from core.permissions import set_untrusted_context
    set_untrusted_context(True)
    return outcome.untrusted("notifications", res.to_speech_text())


TOOL = {
    "name": "notification_center",
    "description": (
        "Reads, checks, and summarizes Windows notifications. "
        "Use whenever the user asks: 'Read my latest notifications', 'Any new notifications?', "
        "'Read my WhatsApp notifications', 'Summarize my notifications', or 'What did my notifications say?'. "
        "Supports filtering by application (app='WhatsApp' or 'Outlook') and summarization (action='summarize')."
    ),
    "brief": "Reads, filters, and summarizes Windows desktop and Action Center notifications.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "read | summarize",
            },
            "app": {
                "type": "STRING",
                "description": "Optional application name to filter notifications by (e.g. 'WhatsApp', 'Outlook', 'Slack').",
            },
            "unread_only": {
                "type": "BOOLEAN",
                "description": "Whether to only return unread notifications (defaults to False).",
            },
        },
        "required": [],
    },
    "handler": notification_center,
}

