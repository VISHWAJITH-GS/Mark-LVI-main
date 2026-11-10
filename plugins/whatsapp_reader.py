"""
plugins/whatsapp_reader.py — Auto-discovered JARVIS plugin for reading and summarizing WhatsApp messages.

Capabilities:
- Read recent incoming WhatsApp messages.
- Identify sender and read message content aloud.
- Read latest message from a specified contact.
- Summarize recent conversations with a contact.
"""
from __future__ import annotations

from typing import Optional
from core import outcome
from plugins import _whatsapp_core as wa


PLUGIN = {
    "name": "whatsapp_read",
    "description": (
        "Reads recent incoming WhatsApp messages or chat history with a specified contact. "
        "Capabilities: "
        "1. Read latest message from a contact: action='read_chat', contact='Mom' "
        "2. Read recent incoming messages across chats: action='read_latest', limit=5 "
        "3. Summarize recent conversation with a contact: action='summarize', contact='Rahul' "
        "Use whenever the user asks 'what did my friend send', 'read my latest WhatsApp message', "
        "'read latest conversation with mom', or 'summarize my chat with X'."
    ),
    "brief": "Reads recent incoming WhatsApp messages, contact chat history, and summarizes conversations.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "read_chat | read_latest | summarize",
            },
            "contact": {
                "type": "STRING",
                "description": "The contact name (e.g. 'Mom', 'Rahul', 'Friend'). Optional for read_latest.",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Number of recent messages to inspect (default 5).",
            },
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "read_chat").lower().strip()
    contact = (params.get("contact") or "").strip()
    limit = int(params.get("limit") or 5)

    driver, why = wa.get()
    if not driver:
        return f"Could not initialize WhatsApp driver: {why}"

    if player and hasattr(player, "write_log"):
        player.write_log(f"JARVIS: Reading WhatsApp messages ({action})…")

    # 1. Summarize conversation
    if action in ("summarize", "summarize_chat", "summary"):
        if not contact:
            return "Please tell me which contact's conversation you'd like me to summarize."
        ok, summary = driver.summarize_conversation(contact)
        if ok:
            if player and hasattr(player, "write_log"):
                player.write_log(f"JARVIS: Summary of chat with {contact}: {summary}")
            return f"Here is the summary of your recent conversation with {contact}: {summary}"
        return f"Could not summarize chat with {contact}: {summary}"

    # 2. Read specific contact chat
    if contact or action in ("read_chat", "chat_history"):
        if not contact:
            # Fallback to latest incoming if no contact specified
            ok, msgs, err = driver.read_latest_incoming_messages(limit=limit)
        else:
            ok, msgs, err = driver.read_chat_messages(contact_name=contact, limit=limit)

        if not ok or not msgs:
            return f"Could not read messages for {contact or 'the current chat'}: {err}"

        lines = []
        for m in msgs:
            lines.append(f"{m['sender']}: {m['text']}")

        spoken_summary = f"Recent messages with {contact or 'WhatsApp'}:\n" + "\n".join(lines)
        if player and hasattr(player, "write_log"):
            player.write_log(f"JARVIS: {spoken_summary}")
        return spoken_summary

    # 3. Read latest incoming across chats
    ok, msgs, err = driver.read_latest_incoming_messages(limit=limit)
    if not ok or not msgs:
        return f"Could not retrieve recent incoming messages: {err}"

    lines = [f"{m['sender']}: {m['text']}" for m in msgs]
    return "Your latest WhatsApp messages:\n" + "\n".join(lines)

