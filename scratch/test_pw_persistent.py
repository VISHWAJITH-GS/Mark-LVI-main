import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    profile = Path.home() / ".mark_lvi_test_profile" / "test_inst"
    profile.mkdir(parents=True, exist_ok=True)
    print("Starting playwright...")
    pw = await async_playwright().start()
    print("Launching persistent context...")
    context = await pw.chromium.launch_persistent_context(
        str(profile),
        headless=True,
    )
    print("Context launched:", context)
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto("https://example.com")
    print("Title:", await page.title())
    await context.close()
    await pw.stop()
    print("Done!")

asyncio.run(main())

