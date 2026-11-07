"""
actions/takeover_desktop.py — Autonomous Windows Desktop Takeover Action for Mark-LVI

Exposes the central autonomous agent orchestrator as an auto-discovered action
so it is loaded by core/action_loader.py and registered into the LLM function schemas.
"""
from __future__ import annotations

from core import agent_orchestrator

TOOL = agent_orchestrator.TOOL

