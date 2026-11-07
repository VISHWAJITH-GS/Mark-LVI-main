"""
scripts/test_windows_uia_live.py — Interactive Live Manual Testing Script for Windows UIA.

This script executes real live desktop tests on Windows:
  1. Creates a temporary folder structure:
     - Downloads
     - Academic Information
     - Semester 5
         └─ Applied Image Processing
     - Semester 6
     - Projects
  2. Opens File Explorer to the test location.
  3. Uses Windows UI Automation to locate "Semester 5".
  4. Opens "Semester 5" without clicking adjacent folders.
  5. Verifies the new window state matches "Semester 5".
  6. Opens "Applied Image Processing" inside Semester 5 and verifies.
  7. Tests searching for missing folders ("Semester 99") and verifies safe failure.
  8. Cleans up test directories.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from actions import windows_uia
from actions.computer_control import computer_control


def main():
    print("=" * 60)
    print("MARK LVI — Windows UI Automation Live Desktop Test")
    print("=" * 60)

    if not windows_uia.is_available():
        print("❌ Windows UI Automation is not available on this system.")
        print("   Make sure you are running on Windows with pywinauto installed.")
        return 1

    metrics = windows_uia.get_screen_metrics()
    print(f"🖥️  Screen Metrics:")
    print(f"   Virtual Desktop: {metrics['virtual_w']}x{metrics['virtual_h']}")
    print(f"   Display Scale:   {int(metrics['scale_factor'] * 100)}%")
    print(f"   PyAutoGUI Size:  {metrics['pyautogui_w']}x{metrics['pyautogui_h']}")

    # 1. Setup temporary test directory structure
    test_root = Path(tempfile.gettempdir()) / "jarvis_uia_live_test"
    if test_root.exists():
        shutil.rmtree(test_root, ignore_errors=True)
    test_root.mkdir(parents=True, exist_ok=True)

    folders = [
        "Downloads",
        "Academic Information",
        "Semester 5",
        "Semester 6",
        "Projects",
    ]
    for f in folders:
        (test_root / f).mkdir(parents=True, exist_ok=True)

    # Subfolder inside Semester 5
    subfolder = test_root / "Semester 5" / "Applied Image Processing"
    subfolder.mkdir(parents=True, exist_ok=True)

    print(f"\n📁 Created test environment at: {test_root}")
    print("   Folders created: " + ", ".join(folders))

    # 2. Launch File Explorer to test_root
    print("\n▶ Step 1: Launching File Explorer...")
    subprocess.Popen(["explorer.exe", str(test_root)])
    time.sleep(2.0)

    # 3. Check active window
    print("\n▶ Step 2: Inspecting active window...")
    active = windows_uia.get_active_window()
    print(f"   Active Window: '{active.get('title')}' (Process: {active.get('process_name')})")

    # 4. Find Semester 5 using UIA
    print("\n▶ Step 3: Searching for 'Semester 5' via Windows UIA...")
    find_res = windows_uia.find_element("Semester 5", exact=False)
    if find_res.get("status") == "success":
        match = find_res.get("match", {})
        print(f"   ✅ Found element: '{match.get('name')}' ({match.get('control_type')})")
        print(f"      Bounding Box: {match.get('bounding_rectangle')}")
    else:
        print(f"   ⚠️ Could not find 'Semester 5' directly: {find_res.get('reason')}")

    # 5. Open Semester 5
    print("\n▶ Step 4: Opening 'Semester 5' via open_file_explorer_item...")
    open_res = windows_uia.open_file_explorer_item("Semester 5", timeout=3.0)
    print(f"   Result: {open_res}")

    if "status=done" in open_res:
        print("   ✅ Successfully opened and verified 'Semester 5'!")
    elif "status=failed" in open_res:
        print(f"   ❌ Failed to open 'Semester 5': {open_res}")

    # 6. Open nested subfolder 'Applied Image Processing'
    time.sleep(1.0)
    print("\n▶ Step 5: Opening nested folder 'Applied Image Processing'...")
    sub_res = windows_uia.open_file_explorer_item("Applied Image Processing", timeout=3.0)
    print(f"   Result: {sub_res}")

    if "status=done" in sub_res:
        print("   ✅ Successfully opened and verified 'Applied Image Processing'!")

    # 7. Test missing folder detection (should never guess adjacent items)
    print("\n▶ Step 6: Testing non-existent folder 'Semester 99'...")
    missing_res = windows_uia.open_file_explorer_item("Semester 99", timeout=1.0)
    print(f"   Result: {missing_res}")
    if "status=failed" in missing_res:
        print("   ✅ Non-existent folder safely reported as not found without guessing.")

    # 8. Cleanup
    print("\n🧹 Cleanup: Removing test folder...")
    shutil.rmtree(test_root, ignore_errors=True)
    print("✅ Live test completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

