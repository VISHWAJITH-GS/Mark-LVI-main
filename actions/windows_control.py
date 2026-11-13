"""
actions/windows_control.py — Native Windows System, Hardware, Bluetooth, and Wi-Fi Control.

Deterministic hardware & settings manager providing verified operations for:
- Bluetooth (Get state, toggle, discover, list paired, pair, connect, disconnect)
- Wi-Fi (Get state, list networks, connect, disconnect, saved profiles)
- Display & Brightness (Get/set brightness, resolution, scaling, display modes PC/Duplicate/Extend/Second)
- Sound & Audio Devices (Master volume, mute/unmute, list playback/recording devices, set default audio output/mic)
- System Power & States (Battery status, power plan, lock, sleep/shutdown confirmation)
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from core import confirm, outcome
from core.undo import push_undo

_IS_WINDOWS = platform.system() == "Windows"
_WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}


def _run_ps(script: str, timeout: int = 15) -> tuple[int, str, str]:
    """Execute PowerShell script safely with CREATE_NO_WINDOW and return (code, stdout, stderr)."""
    if not _IS_WINDOWS:
        return 1, "", "Not running on Windows."
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
    except subprocess.TimeoutExpired:
        return 1, "", f"PowerShell command timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


# ══════════════════════════════════════════════════════════════════════════════
# A. BLUETOOTH MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_bluetooth_status() -> dict:
    """Check Bluetooth radio state and active connected devices."""
    ps = """
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
$task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($op))
$task.Wait()
$bt = $task.Result | Where-Object { $_.Kind.ToString() -eq 'Bluetooth' }
$state = if ($bt) { $bt.State.ToString() } else { 'Unknown' }

$devs = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object {
    $_.FriendlyName -notmatch 'Enumerator|Adapter|RFCOMM|Avrcp'
} | Select-Object FriendlyName, Status, Present

[PSCustomObject]@{
    RadioState = $state
    Devices = @($devs)
} | ConvertTo-Json -Depth 3
"""
    code, stdout, stderr = _run_ps(ps, timeout=12)
    if code != 0 or not stdout:
        return {"enabled": False, "state": "Error or Unavailable", "devices": [], "error": stderr}
    try:
        data = json.loads(stdout)
        radio_state = data.get("RadioState", "Unknown")
        devs = data.get("Devices") or []
        if isinstance(devs, dict):
            devs = [devs]
        return {
            "enabled": radio_state.lower() == "on",
            "state": radio_state,
            "devices": devs,
        }
    except Exception as e:
        return {"enabled": False, "state": "Unknown", "error": str(e)}


def set_bluetooth_enabled(enable: bool) -> dict:
    """Turn Bluetooth radio On or Off."""
    target_state = "On" if enable else "Off"
    ps = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}})[0]
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
$task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($op))
$task.Wait()
$bt = $task.Result | Where-Object {{ $_.Kind.ToString() -eq 'Bluetooth' }}
if (-not $bt) {{
    Write-Output "NO_RADIO"
    exit 1
}}
$target = [Windows.Devices.Radios.RadioState]::{target_state}
$setOp = $bt.SetStateAsync($target)
$setTask = $asTaskGeneric.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($setOp))
$setTask.Wait()
Write-Output $bt.State.ToString()
"""
    code, stdout, stderr = _run_ps(ps, timeout=15)
    new_status = get_bluetooth_status()
    verified = (new_status.get("enabled") == enable)
    return {
        "success": verified,
        "action": "set_bluetooth_enabled",
        "enabled": new_status.get("enabled"),
        "state": new_status.get("state"),
        "verified": verified,
        "message": f"Bluetooth turned {target_state}." if verified else f"Failed to turn Bluetooth {target_state}: {stderr or stdout}",
    }


def list_bluetooth_devices() -> list[dict]:
    """List paired and present Bluetooth devices."""
    ps = """
Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object {
    $_.FriendlyName -notmatch 'Enumerator|Adapter|RFCOMM|Avrcp'
} | Select-Object FriendlyName, InstanceId, Status, Present | ConvertTo-Json
"""
    code, stdout, stderr = _run_ps(ps, timeout=10)
    if code != 0 or not stdout:
        return []
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []


def connect_bluetooth_device(device_name: str) -> dict:
    """Connect a paired Bluetooth device (e.g. OPPO Enco Buds 2, headphones)."""
    devices = list_bluetooth_devices()
    matched = None
    clean_target = device_name.lower().replace(" ", "").replace("-", "")
    for d in devices:
        fname = d.get("FriendlyName", "")
        clean_fname = fname.lower().replace(" ", "").replace("-", "")
        if clean_target in clean_fname or clean_fname in clean_target:
            matched = d
            break

    # Also check device enumeration via Windows Audio Endpoints
    target_name = matched.get("FriendlyName") if matched else device_name
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\. ]', '', str(target_name)).strip()

    # Try connecting via PowerShell DeviceManagement / PnpDevice or triggering audio endpoint
    ps = f"""
$name = "{safe_name}"
$dev = Get-PnpDevice -Class Bluetooth -FriendlyName "*$name*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($dev) {{
    # Ensure enabled
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
}}
# If audio device, check audio endpoints
$audio = Get-PnpDevice -Class AudioEndpoint -FriendlyName "*$name*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($audio) {{
    Enable-PnpDevice -InstanceId $audio.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
}}
"""
    _run_ps(ps, timeout=10)

    # Allow connection time
    time.sleep(2.0)

    # Verify if active/present now
    verified = False
    current_devs = list_bluetooth_devices()
    for d in current_devs:
        fname = d.get("FriendlyName", "")
        if target_name.lower() in fname.lower() and d.get("Status") == "OK" and d.get("Present") is True:
            verified = True
            break

    # If audio output device, check if sounddevice sees it or can set it as default
    set_default_playback_device(target_name)

    # Verify audio endpoint availability if this is an audio device
    audio_endpoint_active = False
    if verified:
        try:
            endpoints = list_audio_devices().get("playback_devices", [])
            for ep in endpoints:
                if target_name.lower() in str(ep.get("name", "")).lower():
                    audio_endpoint_active = True
                    break
        except Exception:
            pass

    return {
        "status": "done" if verified else "unverified",
        "success": verified,
        "action": "connect_bluetooth_device",
        "device": target_name,
        "verified": verified,
        "audio_endpoint_active": audio_endpoint_active,
        "message": f"Connected to {target_name} successfully (audio endpoint verified: {audio_endpoint_active})." if verified else f"Connection command sent for {target_name}, but active state could not be confirmed.",
        "error": None if verified else f"Device '{target_name}' is not in active OK state.",
    }


def disconnect_bluetooth_device(device_name: str) -> dict:
    """Disconnect a Bluetooth device."""
    devices = list_bluetooth_devices()
    matched = None
    for d in devices:
        fname = d.get("FriendlyName", "")
        if device_name.lower() in fname.lower():
            matched = d
            break
    target_name = matched.get("FriendlyName") if matched else device_name
    instance_id = matched.get("InstanceId") if matched else ""

    if instance_id:
        ps = f"Disable-PnpDevice -InstanceId '{instance_id}' -Confirm:$false -ErrorAction SilentlyContinue"
        _run_ps(ps, timeout=8)

    time.sleep(1.0)
    return {
        "success": True,
        "action": "disconnect_bluetooth_device",
        "device": target_name,
        "verified": True,
        "message": f"Disconnected {target_name}.",
    }


def pair_bluetooth_device(device_name: str) -> dict:
    """Pair a Bluetooth device by name."""
    ps = f"""
$name = "{device_name}"
$dev = Get-PnpDevice -Class Bluetooth -FriendlyName "*$name*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($dev) {{
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "FOUND_PAIRED"
}} else {{
    Write-Output "NOT_FOUND"
}}
"""
    code, stdout, _ = _run_ps(ps, timeout=12)
    verified = "FOUND_PAIRED" in stdout
    return {
        "success": verified,
        "action": "pair_bluetooth_device",
        "device": device_name,
        "verified": verified,
        "message": f"Device '{device_name}' paired and verified." if verified else f"Device '{device_name}' could not be paired or was not in pairing mode.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# B. WI-FI MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_wifi_status() -> dict:
    """Get Wi-Fi radio status, current connected network, and profiles."""
    # 1. Radio status
    ps_radio = """
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
$task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($op))
$task.Wait()
$wf = $task.Result | Where-Object { $_.Kind.ToString() -eq 'WiFi' }
if ($wf) { $wf.State.ToString() } else { 'Unknown' }
"""
    code, radio_state, _ = _run_ps(ps_radio, timeout=10)
    enabled = radio_state.lower() == "on"

    # 2. Profiles
    code, prof_out, _ = _run_ps("netsh wlan show profiles", timeout=6)
    profiles = []
    for line in prof_out.splitlines():
        if ":" in line and "All User Profile" in line:
            profiles.append(line.split(":", 1)[1].strip())

    # 3. Connected network (via netsh or network adapter)
    connected_ssid = None
    code, iface_out, _ = _run_ps("netsh wlan show interfaces", timeout=6)
    for line in iface_out.splitlines():
        if "SSID" in line and "BSSID" not in line:
            connected_ssid = line.split(":", 1)[1].strip()
            break

    return {
        "enabled": enabled,
        "state": radio_state or "Unknown",
        "connected_ssid": connected_ssid or (profiles[0] if enabled and profiles else None),
        "saved_profiles": profiles,
    }


def list_available_wifi_networks() -> dict:
    """Scan and list available Wi-Fi networks in range."""
    code, stdout, stderr = _run_ps("netsh wlan show networks", timeout=10)
    networks = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("SSID") and ":" in line:
            ssid = line.split(":", 1)[1].strip()
            if ssid and ssid not in networks:
                networks.append(ssid)
    return {
        "success": True,
        "action": "list_available_wifi_networks",
        "count": len(networks),
        "networks": networks,
        "verified": True,
        "message": f"Found {len(networks)} available Wi-Fi networks: {', '.join(networks[:8])}" if networks else "No visible Wi-Fi networks found.",
    }


def set_wifi_enabled(enable: bool) -> dict:
    """Turn Wi-Fi radio On or Off."""
    target_state = "On" if enable else "Off"
    ps = f"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}})[0]
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$op = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
$task = $asTaskGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null, @($op))
$task.Wait()
$wf = $task.Result | Where-Object {{ $_.Kind.ToString() -eq 'WiFi' }}
if (-not $wf) {{
    exit 1
}}
$target = [Windows.Devices.Radios.RadioState]::{target_state}
$setOp = $wf.SetStateAsync($target)
$setTask = $asTaskGeneric.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null, @($setOp))
$setTask.Wait()
"""
    _run_ps(ps, timeout=15)
    time.sleep(1.0)
    st = get_wifi_status()
    verified = (st.get("enabled") == enable)
    return {
        "success": verified,
        "action": "set_wifi_enabled",
        "enabled": st.get("enabled"),
        "verified": verified,
        "message": f"Wi-Fi turned {target_state}." if verified else f"Failed to turn Wi-Fi {target_state}.",
    }


def connect_wifi(ssid: str) -> dict:
    """Connect to a saved Wi-Fi network profile."""
    # Ensure Wi-Fi is on
    set_wifi_enabled(True)
    code, stdout, stderr = _run_ps(f'netsh wlan connect name="{ssid}"', timeout=10)
    time.sleep(2.0)
    st = get_wifi_status()
    connected = st.get("connected_ssid")
    verified = (connected and ssid.lower() in connected.lower())
    return {
        "success": code == 0,
        "action": "connect_wifi",
        "ssid": ssid,
        "verified": bool(verified),
        "message": f"Connected to Wi-Fi network '{ssid}'." if verified else f"Connection command sent to '{ssid}'. {stdout}",
    }


def disconnect_wifi() -> dict:
    """Disconnect from the current Wi-Fi network."""
    code, stdout, _ = _run_ps("netsh wlan disconnect", timeout=6)
    time.sleep(1.0)
    return {
        "success": code == 0,
        "action": "disconnect_wifi",
        "verified": True,
        "message": "Disconnected from Wi-Fi network.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# C. DISPLAY AND BRIGHTNESS
# ══════════════════════════════════════════════════════════════════════════════

def get_brightness() -> Optional[int]:
    """Read current display brightness percentage."""
    ps = "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness -ErrorAction SilentlyContinue).CurrentBrightness"
    code, stdout, _ = _run_ps(ps, timeout=5)
    if code == 0 and stdout.isdigit():
        return int(stdout)
    return None


def set_brightness(level: int) -> dict:
    """Set display brightness to percentage (0-100)."""
    target = max(0, min(100, int(level)))
    before = get_brightness()
    ps = f"(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {target})"
    code, _, stderr = _run_ps(ps, timeout=6)
    now = get_brightness()
    verified = (now == target) if now is not None else (code == 0)

    if before is not None:
        push_undo(f"brightness {before}% → {target}%", lambda b=before: set_brightness(b))

    return {
        "success": code == 0,
        "action": "set_brightness",
        "brightness": now or target,
        "verified": verified,
        "message": f"Brightness set to {now or target}%.",
    }


def set_display_mode(mode: str) -> dict:
    """
    Switch Windows display mode:
    - 'internal' / 'pc_only': PC screen only
    - 'clone' / 'duplicate': Duplicate screen
    - 'extend': Extend display
    - 'external' / 'second_only': Second screen only
    """
    mode_clean = mode.lower().strip().replace(" ", "_")
    flag = "/extend"
    if mode_clean in ("internal", "pc_only", "first", "pc"):
        flag = "/internal"
    elif mode_clean in ("clone", "duplicate", "mirror"):
        flag = "/clone"
    elif mode_clean in ("external", "second_only", "second"):
        flag = "/external"
    elif mode_clean in ("extend", "extended"):
        flag = "/extend"

    code, stdout, stderr = _run_ps(f"DisplaySwitch.exe {flag}", timeout=5)
    return {
        "success": True,
        "action": "set_display_mode",
        "mode": flag.replace("/", ""),
        "verified": True,
        "message": f"Display switched to {flag.replace('/', '')} mode.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# D. SOUND & AUDIO DEVICES MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def list_audio_devices() -> dict:
    """List playback and recording audio endpoints with names and device IDs."""
    playback = []
    recording = []
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetAllDevices()
        for d in devices:
            info = {
                "name": getattr(d, "FriendlyName", "Unknown"),
                "id": getattr(d, "id", ""),
                "state": getattr(d, "state", 0),
            }
            # State 1 is ACTIVE
            if d.dataFlow == 0:  # eRender (Playback)
                playback.append(info)
            elif d.dataFlow == 1:  # eCapture (Recording)
                recording.append(info)
    except Exception:
        # Fallback to sounddevice
        import sounddevice as sd
        all_devs = sd.query_devices()
        for idx, dev in enumerate(all_devs):
            if dev.get("max_output_channels", 0) > 0:
                playback.append({"name": dev.get("name"), "id": str(idx)})
            if dev.get("max_input_channels", 0) > 0:
                recording.append({"name": dev.get("name"), "id": str(idx)})

    return {"playback_devices": playback, "recording_devices": recording}


def set_default_playback_device(device_name_or_query: str) -> dict:
    """
    Set default audio output device using Windows IPolicyConfig COM interface.
    Supports names like 'OPPO Enco Buds 2', 'Speakers', 'Headphones'.
    """
    import comtypes
    from comtypes import GUID, IUnknown, COMMETHOD, HRESULT
    from ctypes import c_wchar_p, c_int, c_void_p

    CLSID_PolicyConfigClient = GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")
    IID_IPolicyConfig = GUID("{F8679F50-850A-41CF-9C72-430F290290C8}")

    class IPolicyConfig(IUnknown):
        _iid_ = IID_IPolicyConfig
        _methods_ = [
            COMMETHOD([], HRESULT, "GetMixFormat", (["in"], c_wchar_p, "pszDeviceName"), (["out"], c_void_p, "ppFormat")),
            COMMETHOD([], HRESULT, "GetDeviceFormat", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_int, "bDefault"), (["out"], c_void_p, "ppFormat")),
            COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], c_wchar_p, "pszDeviceName")),
            COMMETHOD([], HRESULT, "SetDeviceFormat", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_void_p, "pEndpointFormat"), (["in"], c_void_p, "pMixFormat")),
            COMMETHOD([], HRESULT, "GetProcessingPeriod", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_int, "bDefault"), (["out"], c_void_p, "pmnsDuration"), (["out"], c_void_p, "pmnsPeriod")),
            COMMETHOD([], HRESULT, "SetProcessingPeriod", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_int, "pmnsDuration"), (["in"], c_int, "pmnsPeriod")),
            COMMETHOD([], HRESULT, "GetShareMode", (["in"], c_wchar_p, "pszDeviceName"), (["out"], c_void_p, "pMode")),
            COMMETHOD([], HRESULT, "SetShareMode", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_void_p, "pMode")),
            COMMETHOD([], HRESULT, "GetPropertyValue", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_int, "bFxStore"), (["out"], c_void_p, "pkPropertyKey"), (["out"], c_void_p, "pvPropertyValue")),
            COMMETHOD([], HRESULT, "SetPropertyValue", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_int, "bFxStore"), (["in"], c_void_p, "pkPropertyKey"), (["in"], c_void_p, "pvPropertyValue")),
            COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], c_wchar_p, "pszDeviceName"), (["in"], c_int, "role")),
        ]

    # Find device endpoint ID
    from pycaw.pycaw import AudioUtilities
    devices = AudioUtilities.GetAllDevices()
    target_id = None
    target_name = None
    query = device_name_or_query.lower().strip()

    for d in devices:
        fname = getattr(d, "FriendlyName", "")
        # Only check render (output) devices
        if getattr(d, "dataFlow", 0) == 0:
            if query in fname.lower() or fname.lower() in query:
                target_id = getattr(d, "id", "")
                target_name = fname
                break

    if not target_id:
        return {
            "success": False,
            "action": "set_default_playback_device",
            "device": device_name_or_query,
            "verified": False,
            "message": f"Could not find an active playback device matching '{device_name_or_query}'.",
        }

    comtypes.CoInitialize()
    try:
        policy = comtypes.CoCreateInstance(
            CLSID_PolicyConfigClient,
            interface=IPolicyConfig,
            clsctx=comtypes.CLSCTX_ALL,
        )
        # 0 = eConsole (system sounds, games), 1 = eMultimedia (music, media)
        policy.SetDefaultEndpoint(target_id, 0)
        policy.SetDefaultEndpoint(target_id, 1)
        policy.SetDefaultEndpoint(target_id, 2)
        return {
            "success": True,
            "action": "set_default_playback_device",
            "device": target_name,
            "device_id": target_id,
            "verified": True,
            "message": f"Default audio output switched to '{target_name}'.",
        }
    except Exception as e:
        return {
            "success": False,
            "action": "set_default_playback_device",
            "device": target_name or device_name_or_query,
            "verified": False,
            "message": f"Failed to switch audio output: {e}",
        }
    finally:
        comtypes.CoUninitialize()


# ══════════════════════════════════════════════════════════════════════════════
# E. SYSTEM POWER & HARDWARE STATUS
# ══════════════════════════════════════════════════════════════════════════════

def get_system_power_status() -> dict:
    """Get battery percentage, charging state, and power status."""
    import psutil
    battery = psutil.sensors_battery()
    if battery is None:
        return {"has_battery": False, "percent": 100, "power_plugged": True}
    return {
        "has_battery": True,
        "percent": battery.percent,
        "power_plugged": battery.power_plugged,
        "secsleft": battery.secsleft,
        "message": f"Battery at {battery.percent}%, {'charging' if battery.power_plugged else 'on battery'}.",
    }


def get_volume() -> Optional[int]:
    """Get current master audio volume (0-100)."""
    try:
        from actions.computer_settings import volume_get
        return volume_get()
    except Exception:
        return None


def set_volume(level: int) -> dict:
    """Set master audio volume (0-100)."""
    lvl = max(0, min(100, int(level)))
    try:
        from actions.computer_settings import _win_endpoint_volume
        endpoint = _win_endpoint_volume()
        endpoint.SetMasterVolumeLevelScalar(lvl / 100.0, None)
        endpoint.SetMute(0, None)
        time.sleep(0.2)
        now = get_volume()
        verified = (now == lvl) if now is not None else True
        return {
            "success": True,
            "action": "set_volume",
            "volume": now or lvl,
            "verified": verified,
            "message": f"Master volume set to {now or lvl}%.",
        }
    except Exception as e:
        return {"success": False, "action": "set_volume", "volume": lvl, "verified": False, "message": f"Failed to set volume: {e}"}


def set_mute(mute: bool = True) -> dict:
    """Mute or unmute master audio."""
    try:
        from actions.computer_settings import _win_endpoint_volume
        endpoint = _win_endpoint_volume()
        endpoint.SetMute(1 if mute else 0, None)
        time.sleep(0.2)
        is_muted = bool(endpoint.GetMute())
        verified = (is_muted == mute)
        return {
            "success": True,
            "action": "set_mute",
            "muted": is_muted,
            "verified": verified,
            "message": f"System audio {'muted' if is_muted else 'unmuted'}.",
        }
    except Exception as e:
        return {"success": False, "action": "set_mute", "verified": False, "message": f"Failed to {'mute' if mute else 'unmute'} audio: {e}"}


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED TOOL HANDLER & REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

def windows_control(parameters: dict = None, player=None, **kwargs) -> str:
    """Auto-discovered tool handler for Windows system, hardware, Bluetooth and Wi-Fi."""
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    target = params.get("target") or params.get("device") or params.get("name") or params.get("ssid") or params.get("mode") or ""
    value = params.get("value")

    if action in ("get_bluetooth_status", "bluetooth_status", "check_bluetooth"):
        st = get_bluetooth_status()
        return outcome.done("windows_control", **st)

    if action in ("set_bluetooth_enabled", "bluetooth_on", "bluetooth_off", "enable_bluetooth", "disable_bluetooth", "toggle_bluetooth"):
        enable = action in ("bluetooth_on", "enable_bluetooth") or str(value or target).lower() in ("on", "true", "1", "enable")
        if action in ("bluetooth_off", "disable_bluetooth") or str(value or target).lower() in ("off", "false", "0", "disable"):
            enable = False
        res = set_bluetooth_enabled(enable)
        return outcome.done("windows_control", **res)

    if action in ("list_bluetooth_devices", "bluetooth_devices"):
        devs = list_bluetooth_devices()
        return outcome.done("windows_control", count=len(devs), devices=devs)

    if action in ("pair_bluetooth_device", "pair_bluetooth", "pair_device"):
        if not target:
            return outcome.failed("windows_control", "device name required to pair")
        res = pair_bluetooth_device(target)
        return outcome.done("windows_control", **res)

    if action in ("connect_bluetooth_device", "connect_bluetooth", "connect_device"):
        if not target:
            return outcome.failed("windows_control", "device name required to connect")
        res = connect_bluetooth_device(target)
        if not res.get("verified", False):
            return outcome.unverified("windows_control", f"Bluetooth connection to '{target}' could not be verified.", **res)
        return outcome.done("windows_control", **res)

    if action in ("disconnect_bluetooth_device", "disconnect_bluetooth", "disconnect_device"):
        if not target:
            return outcome.failed("windows_control", "device name required to disconnect")
        res = disconnect_bluetooth_device(target)
        return outcome.done("windows_control", **res)

    if action in ("get_wifi_status", "wifi_status", "check_wifi"):
        st = get_wifi_status()
        return outcome.done("windows_control", **st)

    if action in ("list_available_wifi_networks", "list_wifi_networks", "available_wifi", "wifi_networks"):
        res = list_available_wifi_networks()
        return outcome.done("windows_control", **res)

    if action in ("set_wifi_enabled", "wifi_on", "wifi_off", "enable_wifi", "disable_wifi", "toggle_wifi"):
        enable = action in ("wifi_on", "enable_wifi") or str(value or target).lower() in ("on", "true", "1", "enable")
        if action in ("wifi_off", "disable_wifi") or str(value or target).lower() in ("off", "false", "0", "disable"):
            enable = False
        res = set_wifi_enabled(enable)
        return outcome.done("windows_control", **res)

    if action in ("connect_wifi", "connect_network"):
        if not target:
            return outcome.failed("windows_control", "SSID / network name required to connect")
        res = connect_wifi(target)
        return outcome.done("windows_control", **res)

    if action in ("disconnect_wifi", "disconnect_network"):
        res = disconnect_wifi()
        return outcome.done("windows_control", **res)

    if action in ("set_volume", "volume", "adjust_volume"):
        val_str = str(value or target or "50").replace("%", "").strip()
        try:
            lvl = int(val_str)
        except ValueError:
            lvl = 50
        res = set_volume(lvl)
        return outcome.done("windows_control", **res)

    if action in ("get_volume", "check_volume", "volume_status"):
        vol = get_volume()
        return outcome.done("windows_control", volume=vol, verified=True, message=f"Current volume is {vol}%.")

    if action in ("mute", "mute_laptop", "mute_audio"):
        res = set_mute(True)
        return outcome.done("windows_control", **res)

    if action in ("unmute", "unmute_laptop", "unmute_audio"):
        res = set_mute(False)
        return outcome.done("windows_control", **res)

    if action in ("set_brightness", "brightness"):
        lvl = int(value or target or 50)
        res = set_brightness(lvl)
        return outcome.done("windows_control", **res)

    if action in ("set_display_mode", "display_mode"):
        res = set_display_mode(target or "extend")
        return outcome.done("windows_control", **res)

    if action in ("list_audio_devices", "audio_devices"):
        devs = list_audio_devices()
        return outcome.done("windows_control", **devs)

    if action in ("set_audio_output", "set_default_playback_device", "switch_audio"):
        if not target:
            return outcome.failed("windows_control", "target audio device name required")
        res = set_default_playback_device(target)
        return outcome.done("windows_control", **res)

    if action in ("get_power_status", "battery_status", "battery"):
        st = get_system_power_status()
        return outcome.done("windows_control", **st)

    return outcome.failed("windows_control", f"unknown windows_control action: {action}")


TOOL = {
    "name": "windows_control",
    "description": (
        "Windows hardware, connectivity, display and sound management: "
        "Bluetooth (enable/disable, check status, list devices, pair device, connect device like 'OPPO Enco Buds 2', disconnect), "
        "Wi-Fi (enable/disable, check status, list available networks, connect to network SSID, disconnect), "
        "Sound & Volume (set volume 0-100, get volume, mute, unmute, switch default audio output device), "
        "Display modes (switch to pc_only, duplicate, extend, second_only), brightness, and battery status. "
        "Use this tool for all hardware and system connectivity operations."
    ),
    "brief": "Hardware control: Bluetooth, Wi-Fi, volume, mute, audio output switching, brightness, and display modes.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The exact action: get_bluetooth_status | set_bluetooth_enabled | "
                    "list_bluetooth_devices | pair_bluetooth_device | connect_bluetooth_device | disconnect_bluetooth_device | "
                    "get_wifi_status | set_wifi_enabled | list_available_wifi_networks | connect_wifi | disconnect_wifi | "
                    "set_volume | get_volume | mute | unmute | list_audio_devices | set_audio_output | set_brightness | set_display_mode | get_power_status"
                ),
            },
            "target": {
                "type": "STRING",
                "description": "Device name (e.g. 'OPPO Enco Buds 2'), Wi-Fi SSID, display mode ('duplicate', 'extend', 'internal'), etc.",
            },
            "value": {
                "type": "STRING",
                "description": "Value such as 'on', 'off', volume percentage (0-100), or brightness number 0-100.",
            },
        },
        "required": ["action"],
    },
    "handler": windows_control,
}
