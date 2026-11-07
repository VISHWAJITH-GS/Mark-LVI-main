"""
plugins/whatsapp_automation.py

Auto-discovered JARVIS plugin for sending formal and flexible WhatsApp messages.
"""

from typing import Optional
from core import outcome, gemini
from plugins import _whatsapp_core as wa


PLUGIN = {
    "name": "whatsapp_send",
    "description": (
        "Send WhatsApp messages to contacts formally, accurately, and flexibly. "
        "CRITICAL: If the user has NOT provided the actual message content yet (e.g. they only said "
        "'send a message to Mom' or 'text Rahul'), DO NOT call this tool with placeholder text. "
        "Instead, ask the user first: 'What message would you like me to send to <contact>?' "
        "Only call this tool once you know the exact message the user wants to send."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "contact": {
                "type": "STRING",
                "description": "The recipient's contact name (e.g. 'Mom', 'Rahul', 'Boss').",
            },
            "message": {
                "type": "STRING",
                "description": "The actual message content specified by the user to send. Must NOT be placeholder text.",
            },
            "formal": {
                "type": "BOOLEAN",
                "description": "Whether to polish/format the message formally and politely. Defaults to True.",
            },
        },
        "required": ["contact", "message"],
    },
}


def _polish_message_formal(raw_message: str) -> str:
    """Make the message formal and polite using LLM or smart rule-based polishing."""
    raw = (raw_message or "").strip()
    if not raw:
        return raw

    # Try asking Gemini / LLM for quick polish
    try:
        prompt = (
            f"Rewrite the following message in a polite, formal, and clear tone suitable for WhatsApp. "
            f"Preserve the exact meaning, do not add filler or quotes, return ONLY the polished message text:\n\n{raw}"
        )
        polished = gemini.text(prompt, tier=gemini.FAST, timeout_ms=4000)
        if polished and polished.strip() and len(polished.strip()) < len(raw) * 3:
            return polished.strip().strip('"').strip("'")
    except Exception:
        pass

    # Clean fallback: capitalize first letter and ensure proper punctuation
    cleaned = raw[0].upper() + raw[1:] if len(raw) > 1 else raw.upper()
    if not cleaned.endswith((".", "!", "?")):
        cleaned += "."
    return cleaned


_PLACEHOLDERS = (
    "enter your message",
    "message for",
    "insert message",
    "your message here",
    "placeholder",
    "default message",
)

def run(parameters: dict, player=None, session_memory=None) -> str:
    contact = (parameters.get("contact") or "").strip()
    raw_message = (parameters.get("message") or "").strip()
    formal = parameters.get("formal", True)

    if not contact:
        return "No contact name was specified for the WhatsApp message. Please tell me who you want to message."
    if not raw_message or any(p in raw_message.lower() for p in _PLACEHOLDERS):
        return f"What message would you like me to send to {contact}?"

    # Format message formally
    final_message = _polish_message_formal(raw_message) if formal else raw_message

    if player:
        try:
            player.write_log(f"JARVIS: Opening WhatsApp for {contact}…")
        except Exception:
            pass

    driver, why = wa.get()
    if not driver:
        return f"Could not initialize WhatsApp driver: {why}"

    sent, err = driver.send_message_to(contact, final_message)
    if sent:
        msg_preview = f'"{final_message}"'
        if player:
            try:
                player.write_log(f"JARVIS: Message sent to {contact}: {msg_preview}")
            except Exception:
                pass
        return f"I have sent the message to {contact}: {msg_preview}"
    else:
        return f"Failed to send the message to {contact}: {err}"

