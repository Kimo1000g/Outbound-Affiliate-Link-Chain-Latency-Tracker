"""Screenshot + DOM evidence — Playwright full-page capture per chain (compliance/legal need pictures).

Stores output/shots/{job_id}/{i}_{device}.png + .html. Graceful no-op when
Playwright browsers are missing (playwright_available=false, never a fake image).
Embed in PDF via outputs page links + API static serving.
"""
from __future__ import annotations
from pathlib import Path


async def capture_shot(url: str, device: str, out_png: str, timeout_s: int = 25) -> dict:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return {"taken": False, "reason": "playwright not installed — pip install playwright && playwright install chromium",
                "path": ""}
    try:
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            ua = None
            ctx_args: dict = {}
            if device.startswith("mobile"):
                ctx_args = {"viewport": {"width": 390, "height": 844}, "is_mobile": True}
            if ua:
                ctx_args["user_agent"] = ua
            ctx = await browser.new_context(**ctx_args)
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=out_png, full_page=True)
            final = page.url
            await browser.close()
            return {"taken": True, "reason": "", "path": out_png, "final_url": final}
    except Exception as e:
        return {"taken": False, "reason": f"{type(e).__name__}: {str(e)[:200]}", "path": ""}
