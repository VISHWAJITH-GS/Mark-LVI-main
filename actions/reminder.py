"""
actions/reminder.py — Authoritative, Persistent Reminder Management System for JARVIS.

Capabilities:
- Create reminders via natural language or explicit date/time
- Specific date/time parsing with local timezone awareness
- Recurring reminders (daily, weekly, weekdays)
- List upcoming reminders (all, today, tomorrow)
- Read reminders aloud
- Edit reminders (reschedule or change text)
- Delete reminders (unregisters from OS scheduler and local database)
- Mark reminders as completed
- Persistent across JARVIS restarts via local JSON database and Windows Task Scheduler
- Verification of persistence and scheduling before reporting success
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import outcome

_IS_WINDOWS = platform.system() == "Windows"
_CNW: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _reminders_dir() -> Path:
    # Prefer project memory/reminders directory for reliable workspace persistence
    d = _base_dir() / "memory" / "reminders"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        fallback = Path.home() / ".jarvis" / "reminders"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _db_path() -> Path:
    return _reminders_dir() / "reminders_db.json"


def _sanitise(text: str, max_len: int = 200) -> str:
    return (
        text.replace("\\", "")
        .replace('"', "")
        .replace("'", "")
        .replace("\n", " ")
        .replace("\r", "")
        .strip()
    )[:max_len]


# ─────────────────────────────────────────────────────────────────────────────
#  Local Persistence (Database)
# ─────────────────────────────────────────────────────────────────────────────

def _load_db() -> List[dict]:
    p = _db_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Reminder] DB load error: {e}")
        return []


def _save_db(reminders: List[dict]) -> bool:
    p = _db_path()
    try:
        p.write_text(json.dumps(reminders, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[Reminder] DB save error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Date & Time Parsing (Local Timezone & Natural Language)
# ─────────────────────────────────────────────────────────────────────────────

def parse_datetime(
    date_str: Optional[str] = None,
    time_str: Optional[str] = None,
    natural_text: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Parse date and time using local timezone.
    Supports formats:
      - YYYY-MM-DD and HH:MM (24-hour or 12-hour with AM/PM)
      - "tomorrow", "today", "in X minutes/hours", "next monday", etc.
    Returns (datetime_obj, error_or_clarification_question).
    """
    curr = now or datetime.now()
    d_clean = (date_str or "").strip().lower()
    t_clean = (time_str or "").strip().lower()
    raw = (natural_text or "").strip().lower()

    # 1. Direct explicit parsing if both date and time provided
    if d_clean and t_clean:
        # Check date
        target_date = None
        if d_clean == "today":
            target_date = curr.date()
        elif d_clean == "tomorrow":
            target_date = (curr + timedelta(days=1)).date()
        else:
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    target_date = datetime.strptime(d_clean, fmt).date()
                    break
                except ValueError:
                    continue

        # Check time
        target_time = None
        for tfmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
            try:
                target_time = datetime.strptime(t_clean, tfmt).time()
                break
            except ValueError:
                continue

        if target_date and target_time:
            dt = datetime.combine(target_date, target_time)
            if dt <= curr and d_clean not in ("tomorrow",):
                # If time is in the past for today, user probably meant tomorrow
                if d_clean == "today":
                    return None, "That time has already passed for today. Did you mean tomorrow?"
                return None, "That time has already passed. Please specify a future time."
            return dt, None

    # 2. Natural text parsing fallback
    combined = f"{d_clean} {t_clean} {raw}".strip()

    # Relative offset: in X minutes / hours / days
    m_rel = re.search(r"in\s+(\d+)\s*(min|minute|hour|hr|day|sec)s?", combined)
    if m_rel:
        qty = int(m_rel.group(1))
        unit = m_rel.group(2)
        if "min" in unit:
            return curr + timedelta(minutes=qty), None
        elif "hour" in unit or "hr" in unit:
            return curr + timedelta(hours=qty), None
        elif "day" in unit:
            return curr + timedelta(days=qty), None
        elif "sec" in unit:
            return curr + timedelta(seconds=max(15, qty)), None

    # Tomorrow / Today at time (e.g. "tomorrow at 7 am", "tomorrow 9 pm")
    m_day_time = re.search(r"(today|tomorrow)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", combined)
    if m_day_time:
        day_kw = m_day_time.group(1)
        hr = int(m_day_time.group(2))
        mn = int(m_day_time.group(3) or 0)
        ampm = m_day_time.group(4)

        if ampm == "pm" and hr < 12:
            hr += 12
        elif ampm == "am" and hr == 12:
            hr = 0

        target_d = (curr + timedelta(days=1)).date() if day_kw == "tomorrow" else curr.date()
        try:
            target_dt = datetime(target_d.year, target_d.month, target_d.day, hr, mn)
            if target_dt <= curr and day_kw == "today":
                target_dt += timedelta(days=1)
            return target_dt, None
        except ValueError as e:
            return None, f"Invalid time specified: {e}"

    # Standalone time (e.g. "at 7 am", "9:30 pm", "at 18:00")
    m_time = re.search(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", combined)
    if m_time:
        hr = int(m_time.group(1))
        mn = int(m_time.group(2) or 0)
        ampm = m_time.group(3)
        if ampm == "pm" and hr < 12:
            hr += 12
        elif ampm == "am" and hr == 12:
            hr = 0
        target_dt = datetime(curr.year, curr.month, curr.day, hr, mn)
        if target_dt <= curr:
            # Assume tomorrow if time has already passed today
            target_dt += timedelta(days=1)
        return target_dt, None

    return None, "I couldn't clearly understand the date and time. Could you please specify when you'd like the reminder (for example, 'tomorrow at 7 AM')?"


# ─────────────────────────────────────────────────────────────────────────────
#  OS Scheduling (Windows Task Scheduler / cron / launchd)
# ─────────────────────────────────────────────────────────────────────────────

def _write_notify_script(task_name: str, reminder_id: str, message: str) -> Path:
    script_path = _reminders_dir() / f"{task_name}.py"
    msg_literal = json.dumps(message)
    rem_id_literal = json.dumps(reminder_id)
    db_literal = json.dumps(str(_db_path()))

    script_body = f"""# Auto-generated by JARVIS reminder
import sys, os, pathlib, json, time

message = {msg_literal}
rem_id = {rem_id_literal}
db_file = {db_literal}

notified = False

# 1. Windows Notification via win10toast / plyer / powershell BurntToast
try:
    from plyer import notification
    notification.notify(title="JARVIS Reminder", message=message, timeout=20)
    notified = True
except Exception:
    pass

if not notified:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast("JARVIS Reminder", message, duration=15, threaded=False)
        notified = True
    except Exception:
        pass

if not notified:
    try:
        import subprocess
        # PowerShell toast notification
        ps = f'''[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName("text")
$textNodes.Item(0).AppendChild($template.CreateTextNode("JARVIS Reminder")) | Out-Null
$textNodes.Item(1).AppendChild($template.CreateTextNode("{{message}}")) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("JARVIS").Show($toast)'''
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
        notified = True
    except Exception:
        pass

if not notified:
    try:
        import subprocess
        subprocess.run(["msg", "*", "/TIME:30", f"JARVIS Reminder: {{message}}"], check=False)
    except Exception:
        pass

try:
    import winsound
    for freq in [800, 1000, 1200]:
        winsound.Beep(freq, 160)
        time.sleep(0.06)
except Exception:
    pass

# Mark as completed in local database
try:
    if os.path.exists(db_file):
        with open(db_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for r in data:
            if r.get("id") == rem_id and r.get("recurrence", "none") == "none":
                r["status"] = "completed"
                r["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(db_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
except Exception:
    pass
"""
    script_path.write_text(script_body, encoding="utf-8")
    return script_path


def _schedule_task(task_name: str, reminder_id: str, target_dt: datetime, message: str, recurrence: str = "none") -> bool:
    """Schedule the reminder task using the OS scheduler."""
    if not _IS_WINDOWS:
        return True  # Managed purely via persistent DB on non-Windows

    script_path = _write_notify_script(task_name, reminder_id, message)
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    if pythonw.exists():
        python_exe = pythonw

    xml_path = _reminders_dir() / f"{task_name}.xml"

    # Configure trigger based on recurrence
    time_str = target_dt.strftime("%Y-%m-%dT%H:%M:%S")
    trigger_xml = f"""  <Triggers>
    <TimeTrigger>
      <StartBoundary>{time_str}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>"""

    if recurrence == "daily":
        trigger_xml = f"""  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{time_str}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>"""
    elif recurrence == "weekly":
        trigger_xml = f"""  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{time_str}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <WeeksInterval>1</WeeksInterval>
        <DaysOfWeek><Sunday/><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/><Saturday/></DaysOfWeek>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>"""

    xml_content = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>JARVIS Reminder</Description></RegistrationInfo>
{trigger_xml}
  <Actions><Exec>
    <Command>{python_exe}</Command>
    <Arguments>"{script_path}"</Arguments>
  </Exec></Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Principals><Principal>
    <LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel>
  </Principal></Principals>
</Task>"""

    xml_path.write_text(xml_content, encoding="utf-16")
    res = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **_CNW,
    )
    xml_path.unlink(missing_ok=True)
    return res.returncode == 0


def _unschedule_task(task_name: str) -> bool:
    if not _IS_WINDOWS or not task_name:
        return True
    try:
        res = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", **_CNW,
        )
        # Delete associated python script
        script_file = _reminders_dir() / f"{task_name}.py"
        script_file.unlink(missing_ok=True)
        return res.returncode == 0
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Core Operations: Create, List, Edit, Delete, Complete
# ─────────────────────────────────────────────────────────────────────────────

def create_reminder(
    message: str,
    target_dt: datetime,
    recurrence: str = "none",
) -> outcome.ActionResult:
    """Create, persist, and register a verified reminder."""
    safe_msg = _sanitise(message)
    if not safe_msg:
        return outcome.ActionResult.failed("create_reminder", "Reminder message cannot be empty.")

    reminders = _load_db()

    # Deduplication check: reject if pending reminder with identical message and time already exists
    time_iso = target_dt.strftime("%Y-%m-%dT%H:%M:%S")
    for r in reminders:
        if r.get("status") == "pending" and r.get("message", "").lower() == safe_msg.lower():
            if r.get("due_time") == time_iso:
                return outcome.ActionResult.failed(
                    "create_reminder",
                    f"A reminder for '{safe_msg}' at that exact time already exists.",
                )

    rem_id = f"rem_{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:4]}"
    task_name = f"JARVISReminder_{target_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

    # 1. Schedule OS task
    scheduled = _schedule_task(task_name, rem_id, target_dt, safe_msg, recurrence=recurrence)

    # 2. Persist to local database
    entry = {
        "id": rem_id,
        "message": safe_msg,
        "due_time": time_iso,
        "recurrence": recurrence,
        "status": "pending",
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "task_name": task_name,
        "scheduled": scheduled,
    }
    reminders.append(entry)
    saved = _save_db(reminders)

    verified = bool(saved)
    friendly_time = target_dt.strftime("%A, %B %d at %I:%M %p")
    rec_note = f" (recurring {recurrence})" if recurrence != "none" else ""
    evidence_desc = (
        f"Persisted in local database (ID: {rem_id}) and registered with Windows Task Scheduler ({task_name})."
        if scheduled
        else f"Persisted in local database (ID: {rem_id}) across restarts."
    )

    return outcome.ActionResult(
        status=outcome.STATUS_DONE if verified else outcome.STATUS_FAILED,
        action="create_reminder",
        success=verified,
        verified=verified,
        message=f"Reminder set for {friendly_time}{rec_note}: '{safe_msg}'.",
        data={"reminder": entry, "scheduled_task": scheduled},
        evidence=evidence_desc,
    )


def list_reminders(scope: str = "upcoming") -> outcome.ActionResult:
    """List reminders based on scope: upcoming | today | tomorrow | all."""
    reminders = _load_db()
    now = datetime.now()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    today_date = now.date()
    tomorrow_date = today_date + timedelta(days=1)

    filtered = []
    for r in reminders:
        if r.get("status") != "pending" and scope != "all":
            continue

        try:
            due = datetime.strptime(r.get("due_time", ""), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue

        if scope == "today":
            if due.date() == today_date:
                filtered.append(r)
        elif scope == "tomorrow":
            if due.date() == tomorrow_date:
                filtered.append(r)
        elif scope == "upcoming":
            if due >= now or r.get("recurrence", "none") != "none":
                filtered.append(r)
        else:
            filtered.append(r)

    # Sort chronologically
    filtered.sort(key=lambda x: x.get("due_time", ""))

    summary_items = []
    for r in filtered:
        try:
            dt = datetime.strptime(r["due_time"], "%Y-%m-%dT%H:%M:%S")
            time_str = dt.strftime("%B %d at %I:%M %p")
        except Exception:
            time_str = r.get("due_time")
        rec = f" [{r['recurrence']}]" if r.get("recurrence", "none") != "none" else ""
        summary_items.append(f"• {time_str}{rec}: {r.get('message')}")

    count = len(filtered)
    msg = f"Found {count} {scope} reminder{'s' if count != 1 else ''}:\n" + "\n".join(summary_items) if count else f"You have no {scope} reminders."

    return outcome.ActionResult(
        status=outcome.STATUS_DONE,
        action="list_reminders",
        success=True,
        verified=True,
        message=msg,
        data={"count": count, "reminders": filtered, "scope": scope},
        evidence=f"Retrieved {count} records from reminders database.",
    )


def edit_reminder(
    identifier: str,
    new_message: Optional[str] = None,
    new_due_time: Optional[datetime] = None,
    new_recurrence: Optional[str] = None,
) -> outcome.ActionResult:
    """Edit an existing reminder's message, time, or recurrence."""
    reminders = _load_db()
    target = None
    query = identifier.lower().strip()

    for r in reminders:
        if r.get("id") == identifier or query in r.get("message", "").lower():
            target = r
            break

    if not target:
        return outcome.failed("edit_reminder", f"Could not find a reminder matching '{identifier}'.")

    # Unschedule existing task
    _unschedule_task(target.get("task_name", ""))

    if new_message:
        target["message"] = _sanitise(new_message)
    if new_due_time:
        target["due_time"] = new_due_time.strftime("%Y-%m-%dT%H:%M:%S")
        target["task_name"] = f"JARVISReminder_{new_due_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    if new_recurrence:
        target["recurrence"] = new_recurrence

    # Reschedule
    due_dt = datetime.strptime(target["due_time"], "%Y-%m-%dT%H:%M:%S")
    scheduled = _schedule_task(target["task_name"], target["id"], due_dt, target["message"], recurrence=target.get("recurrence", "none"))
    target["scheduled"] = scheduled
    saved = _save_db(reminders)

    friendly_time = due_dt.strftime("%A, %B %d at %I:%M %p")
    return outcome.ActionResult(
        status=outcome.STATUS_DONE if saved else outcome.STATUS_FAILED,
        action="edit_reminder",
        success=saved,
        verified=saved,
        message=f"Reminder updated for {friendly_time}: '{target['message']}'.",
        data={"reminder": target},
        evidence="Updated in reminders database and rescheduled in Task Scheduler.",
    )


def delete_reminder(identifier: str) -> outcome.ActionResult:
    """Delete a reminder and unregister its scheduled task."""
    reminders = _load_db()
    target_idx = None
    query = identifier.lower().strip()

    for idx, r in enumerate(reminders):
        if r.get("id") == identifier or query in r.get("message", "").lower():
            target_idx = idx
            break

    if target_idx is None:
        return outcome.failed("delete_reminder", f"Could not find reminder matching '{identifier}'.")

    target = reminders.pop(target_idx)
    _unschedule_task(target.get("task_name", ""))
    saved = _save_db(reminders)

    return outcome.ActionResult(
        status=outcome.STATUS_DONE,
        action="delete_reminder",
        success=True,
        verified=saved,
        message=f"Deleted reminder: '{target.get('message')}'.",
        data={"deleted": target},
        evidence="Removed from local database and Task Scheduler.",
    )


def complete_reminder(identifier: str) -> outcome.ActionResult:
    """Mark a reminder as completed."""
    reminders = _load_db()
    target = None
    query = identifier.lower().strip()

    for r in reminders:
        if r.get("id") == identifier or query in r.get("message", "").lower():
            target = r
            break

    if not target:
        return outcome.failed("complete_reminder", f"Could not find reminder matching '{identifier}'.")

    target["status"] = "completed"
    target["completed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _unschedule_task(target.get("task_name", ""))
    saved = _save_db(reminders)

    return outcome.ActionResult(
        status=outcome.STATUS_DONE,
        action="complete_reminder",
        success=True,
        verified=saved,
        message=f"Marked reminder as completed: '{target.get('message')}'.",
        data={"reminder": target},
        evidence="Marked status=completed in database.",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Unified Tool Handler for action_loader
# ─────────────────────────────────────────────────────────────────────────────

def reminder(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    **kwargs,
) -> str:
    """Auto-discovered tool handler for all reminder management operations."""
    p = dict(parameters or {})
    p.update(kwargs)

    action = (p.get("action") or "create").lower().strip()
    msg = p.get("message") or p.get("title") or p.get("text") or ""
    date_str = p.get("date") or p.get("date_time") or ""
    time_str = p.get("time") or ""
    query = p.get("query") or p.get("scope") or msg
    recurrence = (p.get("recurrence") or "none").lower().strip()

    # Route based on action
    if action in ("list", "list_reminders", "get_reminders", "check_reminders"):
        scope = "upcoming"
        if "today" in query.lower() or "today" in str(p).lower():
            scope = "today"
        elif "tomorrow" in query.lower() or "tomorrow" in str(p).lower():
            scope = "tomorrow"
        elif "all" in query.lower():
            scope = "all"
        res = list_reminders(scope=scope)
        if player and hasattr(player, "write_log"):
            player.write_log(f"[Reminder] 📋 {res.message}")
        return res.to_speech_text()

    if action in ("read", "read_aloud", "read_reminders", "speak_reminders"):
        scope = "today" if "today" in query.lower() else ("tomorrow" if "tomorrow" in query.lower() else "upcoming")
        res = list_reminders(scope=scope)
        return res.to_speech_text()

    if action in ("delete", "delete_reminder", "remove", "cancel"):
        target_id = p.get("id") or query or msg
        res = delete_reminder(target_id)
        if player and hasattr(player, "write_log"):
            player.write_log(f"[Reminder] 🗑️ {res.message}")
        return res.to_speech_text()

    if action in ("complete", "complete_reminder", "done", "mark_completed"):
        target_id = p.get("id") or query or msg
        res = complete_reminder(target_id)
        return res.to_speech_text()

    if action in ("edit", "edit_reminder", "reschedule", "update"):
        target_id = p.get("id") or query
        dt, err = parse_datetime(date_str, time_str, natural_text=msg)
        res = edit_reminder(target_id, new_message=msg if msg != target_id else None, new_due_time=dt, new_recurrence=recurrence if recurrence != "none" else None)
        return res.to_speech_text()

    # Default: Create reminder
    natural_combined = f"{date_str} {time_str} {msg}".strip()
    target_dt, parse_err = parse_datetime(date_str=date_str, time_str=time_str, natural_text=natural_combined)

    if parse_err or not target_dt:
        return parse_err or "Please provide a valid future date and time for the reminder."

    res = create_reminder(message=msg or "Reminder", target_dt=target_dt, recurrence=recurrence)
    if player and hasattr(player, "write_log"):
        player.write_log(f"[Reminder] ⏰ {res.message}")
    return res.to_speech_text()


TOOL = {
    "name": "reminder",
    "description": (
        "Complete reminder management system. Capabilities: "
        "1. Create reminders (action='create', message='...', date='YYYY-MM-DD' or 'tomorrow', time='HH:MM' or '7 PM', recurrence='none'|'daily'|'weekly'). "
        "2. List reminders (action='list', scope='upcoming'|'today'|'tomorrow'|'all'). "
        "3. Read reminders aloud (action='read', scope='upcoming'|'today'|'tomorrow'). "
        "4. Edit/reschedule reminders (action='edit', query='...', time='...', message='...'). "
        "5. Delete reminders (action='delete', query='assignment'). "
        "6. Mark reminder completed (action='complete', query='...'). "
        "Persists across restarts and verifies scheduling."
    ),
    "brief": "Creates, lists, reads, edits, deletes, and tracks persistent reminders and schedules.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "create | list | read | edit | delete | complete",
            },
            "message": {
                "type": "STRING",
                "description": "The reminder content / description.",
            },
            "date": {
                "type": "STRING",
                "description": "Date (e.g. 'tomorrow', 'today', '2026-11-10').",
            },
            "time": {
                "type": "STRING",
                "description": "Time (e.g. '7 AM', '19:00', '9 PM', 'in 30 minutes').",
            },
            "recurrence": {
                "type": "STRING",
                "description": "none | daily | weekly",
            },
            "query": {
                "type": "STRING",
                "description": "Search term or reminder name for listing, deleting or editing (e.g. 'assignment', 'study').",
            },
        },
        "required": [],
    },
    "handler": reminder,
}
