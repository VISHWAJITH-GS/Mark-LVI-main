import asyncio
from playwright.async_api import async_playwright

async def main():
    print("Starting playwright...")
    pw = await async_playwright().start()
    print("Playwright started.")
    print("Launching chromium...")
    browser = await pw.chromium.launch(headless=True)
    print("Browser launched:", browser)
    page = await browser.new_page()
    await page.goto("https://example.com")
    print("Title:", await page.title())
    await browser.close()
    await pw.stop()
    print("Done!")

asyncio.run(main())

