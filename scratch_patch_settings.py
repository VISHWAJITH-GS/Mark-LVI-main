import re
from pathlib import Path

p = Path("actions/computer_settings.py")
text = p.read_text(encoding="utf-8")

# 1. Add get_wifi_status function before toggle_wifi
wifi_funcs = """def get_wifi_status() -> dict:
    \"\"\"Query whether Wi-Fi is turned on or turned off across platforms.\"\"\"
    if _OS == "Darwin":
        try:
            iface = _get_macos_wifi_interface()
            res = subprocess.run(
                ["networksetup", "-getairportpower", iface],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
            )
            is_on = "On" in res.stdout
            return {"found": True, "is_on": is_on, "state_str": "turned on" if is_on else "turned off"}
        except Exception as e:
            return {"found": False, "error": str(e), "state_str": "unknown"}
    elif _OS == "Windows":
        try:
            ps = (
                "$adapters = Get-NetAdapter; "
                "$res = @(); "
                "foreach ($a in $adapters) { "
                "    $res += @{ "
                "        Name = $a.Name; "
                "        Status = $a.Status; "
                "        InterfaceDescription = $a.InterfaceDescription; "
                "        MediaType = $a.PhysicalMediaType "
                "    } "
                "}; "
                "$res | ConvertTo-Json"
            )
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=8, **_WIN_HIDE)
            data = json.loads(proc.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                desc = (item.get("InterfaceDescription") or "").lower()
                name = (item.get("Name") or "").lower()
                media = str(item.get("MediaType") or "")
                if "wi-fi" in name or "wireless" in desc or "802.11" in media or "wlan" in desc:
                    status = item.get("Status", "Unknown")
                    is_on = (status.lower() == "up")
                    return {
                        "found": True,
                        "name": item.get("Name"),
                        "status": status,
                        "is_on": is_on,
                        "state_str": "turned on" if is_on else "turned off"
                    }
        except Exception as e:
            return {"found": False, "error": str(e), "state_str": "unknown"}
    else:
        try:
            res = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
            is_on = "enabled" in res.stdout
            return {"found": True, "is_on": is_on, "state_str": "turned on" if is_on else "turned off"}
        except Exception as e:
            return {"found": False, "error": str(e), "state_str": "unknown"}
    return {"found": False, "state_str": "not found"}


"""

# Insert get_wifi_status before def toggle_wifi():
text = text.replace("def toggle_wifi():", wifi_funcs + "def toggle_wifi():", 1)

# 2. Update ACTION_MAP to include wifi_status / check_wifi
target_map = '"toggle_wifi":         toggle_wifi,'
replacement_map = target_map + '\n    "wifi_status":         get_wifi_status,\n    "check_wifi":          get_wifi_status,'
text = text.replace(target_map, replacement_map, 1)

# 3. Update _ALIASES to include checking wifi
target_alias = '"toggle_wifi":     ("wifi", "wi-fi", "internet off", "internet on"),'
replacement_alias = target_alias + '\n    "wifi_status":     ("check wifi", "wifi status", "is wifi on", "is wifi turned on", "is wi-fi on", "is wifi off", "wifi turned on or off", "check my wifi"),'
text = text.replace(target_alias, replacement_alias, 1)

# 4. Handle wifi_status in computer_settings execution
target_exec = '    if action == "volume_set":'
wifi_exec = """    if action in ("wifi_status", "check_wifi"):
        st = get_wifi_status()
        state_str = st.get("state_str", "unknown")
        name = st.get("name", "Wi-Fi")
        msg = f"Your {name} is currently {state_str}."
        if player and hasattr(player, "show_content"):
            player.show_content("Wi-Fi Status", msg)
        return outcome.done("computer_settings", wanted=action, status=st, message=msg, verified=True)

"""
text = text.replace(target_exec, wifi_exec + target_exec, 1)

# 5. Update TOOL description
target_tool_desc = '"toggle_wifi | "'
replacement_tool_desc = '"toggle_wifi | wifi_status | "'
text = text.replace(target_tool_desc, replacement_tool_desc, 1)

p.write_text(text, encoding="utf-8")
print("Updated actions/computer_settings.py successfully!")
