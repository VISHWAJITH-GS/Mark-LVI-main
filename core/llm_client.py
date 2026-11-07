"""
core/llm_client.py — Deprecated legacy local LLM client.

NOTE: In accordance with the non-negotiable architecture, all local LLM
inference (Ollama, local Llama, Qwen) has been disabled. Google Gemini API
is the sole AI reasoning engine across the system.
"""
from __future__ import annotations

def ensure_ollama_running(*args, **kwargs) -> bool:
    """Disabled: local LLMs are removed from the execution path."""
    return False

def is_model_available(*args, **kwargs) -> bool:
    """Disabled: local LLMs are removed from the execution path."""
    return False

def chat(*args, **kwargs):
    raise RuntimeError("Local LLM inference is disabled. Google Gemini API is the sole reasoning engine.")

def chat_stream(*args, **kwargs):
    raise RuntimeError("Local LLM inference is disabled. Google Gemini API is the sole reasoning engine.")
