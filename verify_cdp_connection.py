#!/usr/bin/env python3
"""
verify_cdp_connection.py — CLI verification script for live Microsoft Edge CDP Browser-Use Engine.

Connects to Microsoft Edge over Chrome DevTools Protocol (CDP):
  1. Verifies connection to http://localhost:9222 (or EDGE_CDP_URL).
  2. Lists all open Edge tabs (contexts[0].pages).
  3. Extracts the top 15 indexed interactive elements from the active tab.
  4. Prints the distilled semantic element map.
"""
import argparse
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from actions.browser_session import get_browser_session_manager, DEFAULT_CDP_URL
from actions.dom_distiller import get_dom_distiller


def main():
    parser = argparse.ArgumentParser(description="Verify Microsoft Edge CDP connection and AXTree distillation.")
    parser.add_argument("--cdp-url", default=None, help=f"CDP endpoint URL (default: {DEFAULT_CDP_URL})")
    args = parser.parse_args()

    session_mgr = get_browser_session_manager(cdp_url=args.cdp_url)
    distiller = get_dom_distiller()

    print("=" * 70)
    print("  JARVIS BROWSER-USE ENGINE — CDP & AXTree Verification")
    print("=" * 70)
    print(f"Target CDP Endpoint: {session_mgr.cdp_url}")
    print()

    # 1. Connection Guard Check
    print("[1/3] Testing CDP Endpoint Connection...")
    ok, status_msg = session_mgr.check_connection(timeout=2.0)
    if not ok:
        print()
        print("❌ CONNECTION FAILED:")
        print("-" * 70)
        print(status_msg)
        print("-" * 70)
        sys.exit(1)

    print(f"✅ Success: {status_msg}")
    print()

    # 2. Enumerate Open Tabs
    print("[2/3] Querying Open Edge Tabs (contexts[0].pages)...")
    try:
        tabs = session_mgr.list_tabs()
        if not tabs:
            print("⚠️  Connected to Edge context, but no open tabs found.")
        else:
            print(f"Found {len(tabs)} open tab(s):")
            for t in tabs:
                active_marker = " [ACTIVE TAB]" if t.is_active else ""
                print(f"  [{t.index}] {t.title}")
                print(f"       URL: {t.url}{active_marker}")
    except Exception as e:
        print(f"❌ Failed to enumerate tabs: {e}")
        sys.exit(1)
    print()

    # 3. Distill Active Tab (AXTree + DOM Snapshot)
    print("[3/3] Distilling Active Tab (AXTree + DOM Snapshot Perception)...")
    try:
        page = session_mgr.active_page
        cdp_session = session_mgr.get_cdp_session()
        serialized_text, element_map = session_mgr.run_coroutine(
            distiller.distill_page(page, cdp_session=cdp_session, enable_som_overlay=False)
        )

        total_elements = len(element_map)
        print(f"Detected {total_elements} interactive element(s) in viewport.")
        print()

        top_n = min(15, total_elements)
        print(f"--- Top {top_n} Indexed Elements ---")
        for i in range(1, top_n + 1):
            elem = element_map[i]
            box = elem.bounding_box
            print(f"  {elem.to_prompt_line()}")
            print(f"       Bounds: x={box.x:.1f}, y={box.y:.1f}, w={box.width:.1f}, h={box.height:.1f} | backendNodeId={elem.backend_node_id}")

        if total_elements > top_n:
            print(f"  ... and {total_elements - top_n} more element(s).")

    except Exception as e:
        print(f"❌ Failed during page distillation: {e}")
        sys.exit(1)

    print()
    print("=" * 70)
    print("✅ VERIFICATION COMPLETE: CDP + AXTree Browser-Use Engine is active.")
    print("=" * 70)


if __name__ == "__main__":
    main()

