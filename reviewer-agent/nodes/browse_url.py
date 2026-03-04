"""
Node: browse_url

Optionally visits URLs found in the deliverable content to verify they are live
and reachable. This provides additional evidence for the PASS/FAIL verdict.

Extracts URLs using regex, then issues HTTP GET requests to check status.
Handles timeouts gracefully — a non-reachable URL is noted but doesn't auto-fail.
"""

from __future__ import annotations
import re
import httpx
from state import ReviewerState

# Regex to find URLs in text
_URL_RE = re.compile(
    r"https?://[^\s\)\"\'<>]+",
    re.IGNORECASE,
)

MAX_URLS_TO_CHECK = 3  # Don't spam-check too many URLs
TIMEOUT_SECONDS = 10.0


def browse_url(state: ReviewerState) -> dict:
    """Check URLs found in the deliverable content.

    Extracts up to 3 URLs from the deliverable content and verifies each
    one is reachable via HTTP GET. Results are stored in state for use by
    the verdict generation step.
    """
    content = state.get("deliverable_content", "")
    if not content:
        return {"url_check_results": {}}

    # Find URLs in the deliverable
    urls = _URL_RE.findall(content)
    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for url in urls:
        url = url.rstrip(".,;)")  # Strip trailing punctuation
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
        if len(unique_urls) >= MAX_URLS_TO_CHECK:
            break

    if not unique_urls:
        print("  [browse_url] No URLs found in deliverable content")
        return {"url_check_results": {}}

    results = {}
    print(f"  [browse_url] Checking {len(unique_urls)} URL(s): {unique_urls}")

    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as http:
        for url in unique_urls:
            try:
                response = http.get(url)
                status_code = response.status_code
                ok = 200 <= status_code < 400
                results[url] = {
                    "status_code": status_code,
                    "reachable": ok,
                    "final_url": str(response.url),
                }
                print(f"  [browse_url] {url} → {status_code} ({'OK' if ok else 'ERROR'})")
            except httpx.TimeoutException:
                results[url] = {
                    "status_code": None,
                    "reachable": False,
                    "error": "Request timed out after 10s",
                }
                print(f"  [browse_url] {url} → TIMEOUT")
            except httpx.RequestError as exc:
                results[url] = {
                    "status_code": None,
                    "reachable": False,
                    "error": str(exc),
                }
                print(f"  [browse_url] {url} → CONNECTION ERROR: {exc}")

    return {"url_check_results": results}
