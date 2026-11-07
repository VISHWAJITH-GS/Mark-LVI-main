"""
Action discovery, validation, and dispatch — the built-in twin of plugin_loader.

Every actions/*.py that exposes a module-level ``TOOL`` dict is auto-discovered
here, exactly like a drop-in plugin, so main.py never has to hardcode a tool
declaration or a dispatch branch for it. Adding a new bundled action is then the
same one-file operation as writing a plugin: define ``TOOL`` and a handler.

``TOOL`` shape (see actions/open_app.py for a live example):

    TOOL = {
        "name":        "open_app",              # unique, ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
        "description":  "...",                   # what Gemini reads to route the call
        "parameters":  {"type": "OBJECT", ...}, # Gemini function-declaration schema
        "handler":      open_app,                # the callable to run
    }

The handler is invoked through signature introspection: it receives ``parameters``
plus whichever of ``player`` / ``speak`` / ``response`` / ``session_memory`` it
actually declares — so existing action signatures work unchanged.

Discovery runs once at startup; import errors, validation errors, and name
collisions are logged and the offending file is skipped — they NEVER raise out
of discover_actions() and never abort the scan of the remaining files.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
import time
from core import outcome

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}
_CTX_KEYS = ("player", "speak", "response", "session_memory")


# A tool may declare that the model should NOT be held up waiting for it.
# `behavior` goes to the API with the declaration; `scheduling` decides when the
# eventual result is allowed back into the conversation:
#   WHEN_IDLE  — wait for a gap in the speech (the sane default)
#   SILENT     — record it, do not prompt a reply (the tool already announced)
#   INTERRUPT  — cut in immediately (only when the answer cannot wait)
_BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
_SCHEDULING = ("WHEN_IDLE", "SILENT", "INTERRUPT")


# Keys this project puts on a declaration for its own bookkeeping. They live
# here because this file documents what a TOOL dict may contain, and both
# loaders produce them.
#
# THEY MUST NEVER REACH A VENDOR. Gemini validates function declarations
# against a fixed schema and an unknown field is an error, not something it
# ignores — so main.py calls strip_meta() before building the Live config.
#
# The definition is here rather than in providers/schema.py, which is the only
# other place that cares, because main.py must be able to strip them WITHOUT
# importing providers: a default install never loads that package, and the
# measured cost of deciding not to is the release's headline number.
META_KEYS = ("tier", "brief")


def strip_meta(decls):
    """Declarations without META_KEYS. Non-dict entries pass through untouched;
    a declaration carrying none is returned as the same object, so what an API
    receives is byte-for-byte what it received before these keys existed."""
    out = []
    for decl in decls:
        if isinstance(decl, dict) and any(k in decl for k in META_KEYS):
            out.append({k: v for k, v in decl.items() if k not in META_KEYS})
        else:
            out.append(decl)
    return out


def _opt_upper(value, allowed: tuple[str, ...]) -> Optional[str]:
    v = str(value or "").strip().upper()
    return v if v in allowed else None

def _opt_tier(value) -> Optional[str]:
    """"core" or "extra" from a declaration, or None when it did not say.

    Anything else is treated as "did not say" rather than as an error: a typo
    should fall back to the default ranking, not stop a working tool loading.
    """
    text = str(value or "").strip().lower()
    return text if text in ("core", "extra") else None


def _opt_text(value) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


@dataclass
class ActionRecord:
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(_DEFAULT_PARAMS))
    handler: Optional[Callable] = None
    file: str = ""
    valid: bool = False
    error: str = ""
    behavior: Optional[str] = None     # None = the API's default (blocking)
    scheduling: Optional[str] = None   # None = the API's default (WHEN_IDLE)
    # Read by providers/schema.py when a small model gets a tool budget; never
    # sent to any API — main.py strips both before the Gemini config is built.
    #   tier   "core" | "extra" — this file's own opinion of whether it should
    #          survive a tight budget, instead of that being decided by the
    #          order files happen to be discovered in.
    #   brief  a short description used in place of the long one when a budget
    #          is in force. Nothing is deleted: Gemini still gets the full text.
    tier: Optional[str] = None
    brief: Optional[str] = None
    permission: str = "normal"  # "read_only" | "normal" | "sensitive" | "destructive"

    def validate_parameters(self, parameters: dict) -> tuple[bool, str]:
        schema = self.parameters or {}
        required = schema.get("required", [])
        for req in required:
            if req not in parameters or parameters[req] is None or parameters[req] == "":
                return False, f"Missing required parameter '{req}'"
        return True, ""


class ActionRegistry:
    def __init__(self, actions: dict[str, ActionRecord], logger: Callable[[str], None]):
        self._actions = actions          # name -> ActionRecord, VALID entries only
        self._all_records: list[ActionRecord] = []
        self._logger = logger

    # -- called by main.py at LiveConnectConfig build time --
    def get_tool_declarations(self) -> list[dict]:
        out = []
        for rec in self._actions.values():
            decl = {"name": rec.name, "description": rec.description,
                    "parameters": rec.parameters}
            if rec.behavior:
                decl["behavior"] = rec.behavior
            # Budget metadata. NOT part of any API's schema — main.py calls
            # providers.schema.strip_meta() before the Gemini config is built,
            # and budget() consumes them on the local-engine path.
            if rec.tier:
                decl["tier"] = rec.tier
            if rec.brief:
                decl["brief"] = rec.brief
            out.append(decl)
        return out

    def has(self, name: str) -> bool:
        return name in self._actions

    def has_action(self, name: str) -> bool:
        return self.has(name)

    def scheduling(self, name: str) -> Optional[str]:
        """How this action's result should re-enter the conversation, if it said."""
        rec = self._actions.get(name)
        return rec.scheduling if rec else None

    def names(self) -> set[str]:
        return set(self._actions.keys())

    def get_action_record(self, name: str) -> Optional[ActionRecord]:
        return self._actions.get(name)

    def get_action(self, name: str) -> Optional[ActionRecord]:
        return self.get_action_record(name)

    def _validate_parameters(self, rec: ActionRecord, parameters: dict) -> tuple[bool, str]:
        schema = rec.parameters or {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for req in required:
            if req not in parameters or parameters[req] is None or parameters[req] == "":
                return False, f"Missing required parameter '{req}'"
        return True, ""

    def execute(self, name: str, parameters: dict, ctx: dict | None = None) -> outcome.ActionResult:
        """Authoritative execution method returning structured ActionResult."""
        t0 = time.monotonic()
        rec = self._actions.get(name)
        if rec is None or not rec.valid:
            dur = (time.monotonic() - t0) * 1000
            return outcome.ActionResult(
                status=outcome.STATUS_UNSUPPORTED,
                action=name,
                success=False,
                verified=False,
                message=f"Action '{name}' is not available.",
                error=f"Action '{name}' is not registered or valid.",
                duration_ms=dur,
            )

        val_ok, val_err = self._validate_parameters(rec, parameters or {})
        if not val_ok:
            dur = (time.monotonic() - t0) * 1000
            return outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action=name,
                success=False,
                verified=False,
                message=f"Invalid arguments for {name}: {val_err}",
                error=val_err,
                duration_ms=dur,
            )

        from core import permissions
        perm_level = permissions.get_action_permission(name, parameters)
        if permissions.requires_confirmation(name, parameters) and not parameters.get("confirmed"):
            dur = (time.monotonic() - t0) * 1000
            res = outcome.ActionResult(
                status=outcome.STATUS_NEEDS_CONFIRMATION,
                action=name,
                success=False,
                verified=False,
                message=f"Action '{name}' is classified as {perm_level} and requires explicit confirmation before executing.",
                duration_ms=dur,
            )
            permissions.log_action_audit(name, parameters or {}, perm_level, res.status, res.verified, res.error)
            return res

        try:
            raw = _call_handler(rec.handler, parameters or {}, ctx or {})
            dur = (time.monotonic() - t0) * 1000
            res = outcome.normalize_result(raw, action_name=name, duration_ms=dur)
            permissions.log_action_audit(name, parameters or {}, perm_level, res.status, res.verified, res.error)
            return res
        except Exception as e:
            dur = (time.monotonic() - t0) * 1000
            self._logger(f"Action '{name}' crashed during execute(): {e}")
            traceback.print_exc()
            res = outcome.ActionResult(
                status=outcome.STATUS_FAILED,
                action=name,
                success=False,
                verified=False,
                message=f"Tool '{name}' failed: {e}",
                error=str(e),
                duration_ms=dur,
            )
            permissions.log_action_audit(name, parameters or {}, perm_level, res.status, res.verified, res.error)
            return res

    # -- called by main.py from _execute_tool (backward-compatible string return) --
    def run(self, name: str, parameters: dict, ctx: dict | None = None) -> str:
        res = self.execute(name, parameters, ctx)
        return res.message or res.to_speech_text()


def _call_handler(fn: Callable, parameters: dict, ctx: dict) -> str:
    """Invoke the handler passing only the context kwargs it actually declares
    (or all of them if it has **kwargs), and validate arguments against callable parameters."""
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    # Filter context arguments
    kwargs = {}
    for key in _CTX_KEYS:
        if has_var_kw or key in sig.parameters:
            kwargs[key] = ctx.get(key)

    # Some tools accept (parameters: dict, ...), while others accept direct kwargs (*args / **kwargs)
    if "parameters" in sig.parameters or has_var_kw:
        return fn(parameters=parameters, **kwargs)
    else:
        # If tool expects direct keyword arguments instead of a 'parameters' dict
        clean_args = {k: v for k, v in parameters.items() if has_var_kw or k in sig.parameters}
        return fn(**clean_args, **kwargs)


def _validate(module, filename: str) -> ActionRecord:
    """Returns an ActionRecord; .valid=False + .error set on any problem. Never raises."""
    tool = getattr(module, "TOOL", None)
    if not isinstance(tool, dict):
        return ActionRecord(name=Path(filename).stem, file=filename,
                            error="No module-level TOOL dict (not a discoverable action).")

    name = tool.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return ActionRecord(name=str(name or Path(filename).stem), file=filename,
                            error="TOOL['name'] missing or not a valid identifier.")

    description = tool.get("description")
    if not isinstance(description, str) or not description.strip():
        return ActionRecord(name=name, file=filename,
                            error="TOOL['description'] missing or empty.")

    parameters = tool.get("parameters", _DEFAULT_PARAMS)
    if not isinstance(parameters, dict) or parameters.get("type") != "OBJECT":
        return ActionRecord(name=name, file=filename,
                            error="TOOL['parameters'] must be a dict with \"type\": \"OBJECT\".")

    handler = tool.get("handler")
    if not callable(handler):
        return ActionRecord(name=name, file=filename,
                            error="TOOL['handler'] missing or not callable.")

    return ActionRecord(name=name, description=description.strip(), parameters=parameters,
                        handler=handler, file=filename, valid=True, error="",
                        behavior=_opt_upper(tool.get("behavior"), _BEHAVIORS),
                        scheduling=_opt_upper(tool.get("scheduling"), _SCHEDULING),
                        tier=_opt_tier(tool.get("tier")),
                        brief=_opt_text(tool.get("brief")))


def discover_actions(actions_dir: Path, reserved_names: set[str] | None = None,
                     logger: Callable[[str], None] = print) -> ActionRegistry:
    """
    Scans actions_dir for *.py files (skips files starting with '_'). A file is
    only treated as an action if it exposes a module-level TOOL dict; files
    without one (shared helpers, capture-only modules) are silently ignored.
    Import/validation errors and name collisions are logged and the file is
    skipped — they NEVER raise out of this function.
    """
    reserved = reserved_names or set()
    actions_dir.mkdir(parents=True, exist_ok=True)
    valid: dict[str, ActionRecord] = {}
    all_records: list[ActionRecord] = []

    files = sorted(actions_dir.glob("*.py"), key=lambda p: p.name)  # deterministic order
    for path in files:
        if path.name.startswith("_"):
            continue
        try:
            module_name = f"actions.{path.stem}"
            # Reuse the already-imported module when present so handlers are the
            # same objects the rest of the app holds.
            module = sys.modules.get(module_name)
            if module is None:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError("could not build import spec")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(module_name, None)
                    raise

            if getattr(module, "TOOL", None) is None:
                continue   # not an action file — a helper/capture-only module

            rec = _validate(module, path.name)

            if rec.valid and rec.name in reserved:
                rec = ActionRecord(name=rec.name, file=path.name,
                                   error=f"Name '{rec.name}' collides with a reserved core tool — rejected.")
            elif rec.valid and rec.name in valid:
                other = valid[rec.name].file
                rec = ActionRecord(name=rec.name, file=path.name,
                                   error=f"Name '{rec.name}' already used by action '{other}' — rejected.")

        except Exception as e:
            rec = ActionRecord(name=path.stem, file=path.name,
                               error=f"Failed to load: {e}")
            traceback.print_exc()

        all_records.append(rec)
        if rec.valid:
            valid[rec.name] = rec
            logger(f"Action loaded: {rec.name} ({path.name})")
        else:
            # Only log a rejection if the file actually tried to be an action.
            logger(f"Action rejected: {path.name} — {rec.error}")

    registry = ActionRegistry(valid, logger)
    registry._all_records = all_records
    logger(f"Action discovery complete: {len(valid)} active.")
    return registry


_default_registry: Optional[ActionRegistry] = None


def get_default_registry(actions_dir: Path | None = None) -> ActionRegistry:
    """Return or create the authoritative shared ActionRegistry instance."""
    global _default_registry
    if _default_registry is None:
        p = actions_dir or (Path(__file__).resolve().parent.parent / "actions")
        _default_registry = discover_actions(p)
    return _default_registry
