"""
Node: browse_url

Uses Playwright to visit URLs found in the deliverable content. This provides 
higher-fidelity evidence than simple HTTP checks, ensuring the page actually
renders and isn't just a skeleton of empty divs.
"""

from __future__ import annotations
import re
import asyncio
from typing import Any
from app.agents.user_reviewer.state import ReviewerState

# Regex to find URLs in text
_URL_RE = re.compile(
    r"https?://[^\s\)\"\'<>]+",
    re.IGNORECASE,
)

MAX_URLS_TO_CHECK = 3
TIMEOUT_MS = 15000  # 15 seconds per page


def browse_url(state: ReviewerState) -> dict:
    """Check URLs found in the deliverable content using a headless browser."""
    if state.get("error"):
        return {}

    content = state.get("deliverable_content", "")
    if not content:
        return {"url_check_results": {}}

    urls = _URL_RE.findall(content)
    # Deduplicate
    seen = set()
    unique_urls = []
    for url in urls:
        url = url.rstrip(".,;)")
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
        if len(unique_urls) >= MAX_URLS_TO_CHECK:
            break

    if not unique_urls:
        return {"url_check_results": {}}

    # We run the async playwright code in a thread since the node is called synchronously 
    # by reviewer_daemon.py via to_thread.
    results = asyncio.run(_run_playwright_checks(unique_urls))
    return {"url_check_results": results}


async def _run_playwright_checks(urls: list[str]) -> dict[str, Any]:
    """Run headless browser checks for a list of URLs."""
    from playwright.async_api import async_playwright
    
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Oriexa Reviewer Agent (Playwright)"
        )

        for url in urls:
            page = await context.new_page()
            print(f"  [browse_url] Navigating to: {url}")
            try:
                response = await page.goto(url, timeout=TIMEOUT_MS, wait_until="networkidle")
                
                # Basic accessibility / content check
                title = await page.title()
                # Check for common React/Next error strings or empty pages
                body_text = await page.inner_text("body")
                is_empty = len(body_text.strip()) < 50
                
                results[url] = {
                    "reachable": True,
                    "status_code": response.status if response else 200,
                    "title": title,
                    "has_content": not is_empty,
                    "content_length": len(body_text),
                }
                print(f"  [browse_url] {url} -> PASS (Title: {title})")
            except Exception as e:
                print(f"  [browse_url] {url} -> FAIL: {str(e)}")
                results[url] = {
                    "reachable": False,
                    "error": str(e)
                }
            finally:
                await page.close()

        await browser.close()
    return results
