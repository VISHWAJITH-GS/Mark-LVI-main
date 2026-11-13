import json
import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def get_user_data_dir() -> Path:
    """Return the per-user runtime data directory outside the repository."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "JARVIS"
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "JARVIS"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "jarvis"
    base.mkdir(parents=True, exist_ok=True)
    return base

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"
CONFIG_TEMPLATE = CONFIG_DIR / "api_keys.template.json"

_KEYRING_SERVICE = "JARVIS_Mark_LVI"
_KEYRING_USER    = "gemini_api_key"

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),               # Google API Key pattern
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),                 # Standard secret key prefix
]

def _is_keyring_available() -> bool:
    """Check if keyring package is available with an active backend."""
    try:
        import keyring
        import keyring.backends.fail
        b = keyring.get_keyring()
        if isinstance(b, keyring.backends.fail.Keyring):
            return False
        return True
    except Exception:
        return False


def _restrict_file_permissions(path: Path) -> None:
    """Restrict access permissions to the owner where the OS allows."""
    try:
        if not path.exists():
            return
        if platform.system() == "Windows":
            # Best effort ACL restriction on Windows
            try:
                user = os.getlogin()
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def redact_secret(text: str) -> str:
    """Redact sensitive API keys and secrets from logs and messages."""
    if not isinstance(text, str) or not text:
        return text
    redacted = text
    # 1. Redact known stored keys
    try:
        key = get_gemini_key()
        if key and len(key) >= 8 and key in redacted:
            redacted = redacted.replace(key, "[REDACTED_API_KEY]")
    except Exception:
        pass
    # 2. Redact regex patterns
    for pat in _SECRET_PATTERNS:
        redacted = pat.sub("[REDACTED_SECRET]", redacted)
    return redacted


def scan_for_secrets(text: str) -> list[str]:
    """Return any suspicious secret strings identified in text."""
    if not isinstance(text, str) or not text:
        return []
    matches = []
    try:
        key = get_gemini_key()
        if key and len(key) >= 8 and key in text:
            matches.append("Configured Gemini API key detected in text")
    except Exception:
        pass
    for pat in _SECRET_PATTERNS:
        found = pat.findall(text)
        if found:
            matches.extend(found)
    return matches


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def ensure_config_exists() -> None:
    ensure_config_dir()
    if not CONFIG_FILE.exists() and CONFIG_TEMPLATE.exists():
        try:
            CONFIG_FILE.write_text(CONFIG_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

TRUST_LEVEL_STRICT   = "strict"
TRUST_LEVEL_BALANCED = "balanced"
TRUST_LEVEL_TRUSTED  = "trusted"
VALID_TRUST_LEVELS   = (TRUST_LEVEL_STRICT, TRUST_LEVEL_BALANCED, TRUST_LEVEL_TRUSTED)

def get_trust_level() -> str:
    """Return user trust level: 'strict', 'balanced' (default), or 'trusted'."""
    val = str(load_api_keys().get("trust_level", TRUST_LEVEL_BALANCED)).strip().lower()
    return val if val in VALID_TRUST_LEVELS else TRUST_LEVEL_BALANCED

def set_trust_level(level: str) -> None:
    """Persist user trust level setting."""
    clean = str(level).strip().lower()
    if clean not in VALID_TRUST_LEVELS:
        raise ValueError(f"Invalid trust level '{level}'. Must be one of {VALID_TRUST_LEVELS}")
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["trust_level"] = clean
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

DEFAULT_ALLOWED_SUBNETS = [
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
]

def get_dashboard_allow_lan() -> bool:
    """Return whether the dashboard is allowed to bind to LAN (default False: loopback only)."""
    return bool(load_api_keys().get("dashboard_allow_lan", False))

def set_dashboard_allow_lan(allow: bool) -> None:
    """Configure whether the dashboard binds to LAN or loopback only."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["dashboard_allow_lan"] = bool(allow)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def get_dashboard_allowed_subnets() -> list[str]:
    """Return list of IP CIDR subnets allowed to access the dashboard."""
    subnets = load_api_keys().get("dashboard_allowed_subnets")
    if isinstance(subnets, list) and subnets:
        return [str(s).strip() for s in subnets if str(s).strip()]
    return list(DEFAULT_ALLOWED_SUBNETS)

def set_dashboard_allowed_subnets(subnets: list[str]) -> None:
    """Set allowed IP CIDR subnets for dashboard access."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["dashboard_allowed_subnets"] = [str(s).strip() for s in subnets if str(s).strip()]
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def _migrate_plaintext_key_to_keyring(data: dict) -> bool:
    """Migrate plaintext key from json dict to OS keyring if available."""
    if not _is_keyring_available():
        return False
    plaintext_key = str(data.get("gemini_api_key") or "").strip()
    if plaintext_key:
        try:
            import keyring
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, plaintext_key)
            del data["gemini_api_key"]
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("[Config] 🔒 Auto-migrated Gemini API key to OS credential store.")
            return True
        except Exception as e:
            print(f"[Config] ⚠️ Keyring migration failed: {e}")
            return False
    return False

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()
    clean_key = gemini_api_key.strip()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    if _is_keyring_available():
        try:
            import keyring
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, clean_key)
            # Remove secret key from JSON file so it is never stored plaintext
            data.pop("gemini_api_key", None)
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return
        except Exception as e:
            print(f"[Config] ⚠️ OS Keyring error ({e}) — falling back to restricted file storage.")

    # Fallback path when keyring is unavailable
    print("[Config] ⚠️ OS Keyring unavailable — saving API key to config/api_keys.json with restricted permissions.")
    data["gemini_api_key"] = clean_key
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _restrict_file_permissions(CONFIG_FILE)

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        # Auto-migrate plaintext key if keyring is active
        if "gemini_api_key" in data and _is_keyring_available():
            _migrate_plaintext_key_to_keyring(data)
        return data
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    # 1. Environment variable override
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key and len(env_key.strip()) > 8:
        return env_key.strip()

    # 2. OS Keyring (Windows Credential Manager / Keychain / SecretService)
    if _is_keyring_available():
        try:
            import keyring
            stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
            if stored and len(stored.strip()) > 8:
                return stored.strip()
        except Exception:
            pass

    # 3. Fallback: check config/api_keys.json (and auto-migrate if possible)
    raw_data = {}
    if CONFIG_FILE.exists():
        try:
            raw_data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    file_key = str(raw_data.get("gemini_api_key") or "").strip()
    if file_key:
        if _is_keyring_available():
            _migrate_plaintext_key_to_keyring(raw_data)
        return file_key

    return None

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


def get_listening_shortcut() -> list[str]:
    """Global keyboard shortcut for toggling listening. Default Ctrl+Alt+J."""
    v = load_api_keys().get("global_listening_shortcut")
    if isinstance(v, list) and v:
        return [str(k).lower() for k in v]
    return ["ctrl", "alt", "j"]


def save_listening_shortcut(chord: list[str]) -> None:
    _save_flag("global_listening_shortcut", [str(k).lower() for k in chord])


def get_emergency_stop_shortcut() -> list[str]:
    """Global keyboard shortcut for immediate speech interruption. Default Ctrl+Alt+K."""
    v = load_api_keys().get("global_emergency_stop_shortcut")
    if isinstance(v, list) and v:
        return [str(k).lower() for k in v]
    return ["ctrl", "alt", "k"]


def save_emergency_stop_shortcut(chord: list[str]) -> None:
    _save_flag("global_emergency_stop_shortcut", [str(k).lower() for k in chord])


def get_mouse_gesture_enabled() -> bool:
    """Simultaneous Left+Right mouse button gesture to toggle listening."""
    return load_api_keys().get("global_mouse_gesture_enabled", True)


def save_mouse_gesture_enabled(enabled: bool) -> None:
    _save_flag("global_mouse_gesture_enabled", bool(enabled))


HUD_STYLES = ("face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what MARK LIV shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


def get_ui_scale() -> float:
    """How much larger than designed the whole interface is drawn, or 0 for
    'work it out from the screen'.

    Read once, before the window exists — Qt only takes a scale factor at
    start-up, so a change here applies on the next launch."""
    try:
        v = float(load_api_keys().get("ui_scale", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return min(v, 3.0) if v >= 0.5 else 0.0


def save_ui_scale(scale: float) -> None:
    _save_flag("ui_scale", float(scale or 0))


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")

# ── Providers: local STT/LLM/TTS and non-Gemini APIs ─────────────────────────
# Stored here rather than in a file of their own, for the same reason plugin
# settings are: one config to back up, one already in .gitignore, one place a
# user knows to look.
#
# The important property is what happens when nobody has configured anything.
# `get_provider_mode()` returns "gemini" on a config that has never heard of
# providers, and every caller checks that BEFORE importing the providers
# package — so an ordinary install never executes a line of it. This is the
# whole basis of the claim that the folder costs nothing when unused.
PROVIDER_MODE_GEMINI = "gemini"      # the conversation runs on the Live API
PROVIDER_MODE_PIPELINE = "pipeline"  # STT -> LLM -> TTS, assembled from providers


def _upgrade_ids(block: dict) -> dict:
    """Give every catalog service its own id and its own stored settings.

    One provider serves Ollama, OpenAI, MiniMax and a dozen more, and they used
    to share ONE block of settings under "openai_compat" — so the fallback and
    ② THINKING could not be two different services, picking one overwrote the
    other's address, and an API key typed for MiniMax was sent to OpenAI after
    a switch. Each service is now "<provider>@<entry>", e.g.
    "openai_compat@minimax", with settings of its own.

    An older config is converted as it is read, not by a migration step: the
    shared block moves to the service its "catalog" key names (its key belonged
    to that service and nothing else), and every reference to it follows.
    Writing the providers block back persists it; until then nothing on disk
    changes."""
    creds = block.get("creds")
    if not isinstance(creds, dict):
        return block
    creds = dict(creds)
    moved: dict = {}
    for pid, cfg in list(creds.items()):
        if "@" in pid or not isinstance(cfg, dict):
            continue
        eid = str(cfg.get("catalog") or "").strip()
        if not eid:
            continue
        new = f"{pid}@{eid}"
        creds.setdefault(new, dict(cfg))
        del creds[pid]
        moved[pid] = new
    if not moved:
        return block
    block = dict(block)
    block["creds"] = creds
    pipe = block.get("pipeline")
    if isinstance(pipe, dict):
        block["pipeline"] = {k: moved.get(v, v) for k, v in pipe.items()}
    for key in ("sidecar", "coding"):
        if block.get(key) in moved:
            block[key] = moved[block[key]]
    return block


def get_providers() -> dict:
    """The whole providers block, or an empty dict."""
    val = load_api_keys().get("providers")
    return _upgrade_ids(dict(val)) if isinstance(val, dict) else {}


def get_provider_mode() -> str:
    """Which engine holds the conversation. Strictly Google Gemini."""
    return PROVIDER_MODE_GEMINI


def get_provider_config(provider_id: str) -> dict:
    """Stored settings for one provider — address, model, key, limits."""
    creds = get_providers().get("creds")
    val = creds.get(provider_id) if isinstance(creds, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_pipeline() -> dict:
    """Which provider fills each slot of the pipeline engine."""
    val = get_providers().get("pipeline")
    return dict(val) if isinstance(val, dict) else {}


def get_sidecar_provider() -> str:
    """The provider id used for one-shot calls when Gemini cannot answer them,
    or '' for none.

    Separate from the conversation on purpose: these are different jobs with
    different failure modes. Gemini's Live quota and its text quota are separate
    pools, and on the free tier it is the text pool that runs out first — which
    kills document reading, screen reasoning and code generation while the
    conversation carries on. Being able to point only those at another model,
    without changing the voice at all, is the cheapest useful thing this whole
    folder does.
    """
    return str(get_providers().get("sidecar", "") or "").strip()


def _patch_providers(**fields) -> None:
    p = get_providers()
    p.update(fields)
    _patch_config(providers=p)


def save_provider_mode(mode: str) -> None:
    mode = (mode or "").strip().lower()
    _patch_providers(mode=mode if mode in (PROVIDER_MODE_GEMINI,
                                           PROVIDER_MODE_PIPELINE)
                     else PROVIDER_MODE_GEMINI)


def save_provider_config(provider_id: str, values: dict) -> None:
    """Merge settings for one provider. Only the given keys are touched, so the
    panel can save one field without reading and rewriting the rest."""
    p = get_providers()
    creds = p.get("creds")
    if not isinstance(creds, dict):
        creds = {}
    cur = creds.get(provider_id)
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    creds[provider_id] = cur
    p["creds"] = creds
    _patch_config(providers=p)


def save_pipeline(stt: str = None, llm: str = None, tts: str = None) -> None:
    """Set one or more pipeline slots. `None` leaves a slot alone; '' clears it."""
    p = get_providers()
    pipe = p.get("pipeline")
    pipe = dict(pipe) if isinstance(pipe, dict) else {}
    for slot, value in (("stt", stt), ("llm", llm), ("tts", tts)):
        if value is not None:
            pipe[slot] = str(value).strip()
    p["pipeline"] = pipe
    _patch_config(providers=p)


def save_sidecar_provider(provider_id: str) -> None:
    _patch_providers(sidecar=(provider_id or "").strip())


def get_coding_provider() -> str:
    """The service dev_agent writes code with, or '' to use whatever answers
    background requests.

    Its own setting because coding is the one background job where the model
    matters most and the request is longest — somebody may well want a strong
    coding model there and something cheap and fast for reading the screen."""
    return str(get_providers().get("coding", "") or "").strip()


def save_coding_provider(provider_id: str) -> None:
    _patch_providers(coding=(provider_id or "").strip())


def get_provider_fallback_enabled() -> bool:
    """Return whether fallback to alternative/local LLM providers is enabled (default False)."""
    return bool(load_api_keys().get("provider_fallback_enabled", False))


def set_provider_fallback_enabled(enabled: bool) -> None:
    """Configure whether fallback to alternative/local LLM providers is enabled."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["provider_fallback_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_sidecar_first() -> bool:
    """Whether the side-call provider is tried BEFORE Gemini rather than after.

    Off by default: measured in core/gemini.py, the Gemini rungs answer a short
    request in well under a second, and a local model on ordinary hardware does
    not. On means something specific — 'I would rather this stayed on my
    machine, and I accept it being slower' — so it is a choice someone makes,
    never a default they inherit.
    """
    return bool(get_providers().get("sidecar_first", False))


def save_sidecar_first(enabled: bool) -> None:
    _patch_providers(sidecar_first=bool(enabled))


def save_providers(mode: str = None, pipeline: dict = None, creds: dict = None,
                   sidecar: str = None, sidecar_first: bool = None,
                   coding: str = None) -> None:
    """Write the whole providers block in ONE file write.

    The settings panel used to call five setters in a row — one per provider's
    credentials, then the mode, then the pipeline, then the two side-call
    keys — and every one of them was a separate read-modify-write of
    api_keys.json. Five reads and five writes of the same file, in sequence,
    while the panel's own APPLY was already triggering a reconnect that makes
    main.py write to it too. Any interleaving loses whichever key was written
    first, and the one people noticed was the mode: set MY OWN SETUP, save,
    reopen, and it says Gemini again.

    `None` leaves a section alone; `creds` is merged per provider so a panel
    that only edited one does not have to send the others.
    """
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    block = data.get("providers")
    block = _upgrade_ids(dict(block)) if isinstance(block, dict) else {}

    if mode is not None:
        m = str(mode).strip().lower()
        block["mode"] = m if m in (PROVIDER_MODE_GEMINI,
                                   PROVIDER_MODE_PIPELINE) else PROVIDER_MODE_GEMINI
    if pipeline is not None:
        current = block.get("pipeline")
        current = dict(current) if isinstance(current, dict) else {}
        current.update({k: str(v or "").strip() for k, v in pipeline.items()})
        block["pipeline"] = current
    if creds is not None:
        store = block.get("creds")
        store = dict(store) if isinstance(store, dict) else {}
        for pid, values in (creds or {}).items():
            one = store.get(pid)
            one = dict(one) if isinstance(one, dict) else {}
            one.update(values or {})
            store[pid] = one
        block["creds"] = store
    if sidecar is not None:
        block["sidecar"] = str(sidecar or "").strip()
    if sidecar_first is not None:
        block["sidecar_first"] = bool(sidecar_first)
    if coding is not None:
        block["coding"] = str(coding or "").strip()

    data["providers"] = block
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
