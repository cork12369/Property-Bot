"""Browser-driven fetching and pagination for the scraper.

PropertyGuru sits behind Cloudflare, which challenges non-browser clients
(plain HTTP gets 403 even with Chrome TLS impersonation, and headless
Chromium is detected too). The scraper therefore runs a real, visible
Chromium window via Playwright: page 1 loads directly, and further pages
are reached by clicking the pagination "next" button exactly like a human
user, which triggers client-side routing that Cloudflare permits.
"""

from __future__ import annotations

import random
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from .parser import parse_search_page
from .models import Listing

DEFAULT_START_URL = "https://www.propertyguru.com.sg/property-for-sale"

CARD_SELECTOR = 'div[da-id^="parent-listing-card"]'
NEXT_BUTTON_SELECTOR = '[da-id="hui-pagination-btn-next"]'

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
"""


class BlockedError(RuntimeError):
    """Raised when PropertyGuru/Cloudflare appears to be blocking requests."""


def _wait_for_cards(page, timeout_ms: int = 60000) -> None:
    try:
        page.wait_for_selector(CARD_SELECTOR, timeout=timeout_ms)
    except PlaywrightTimeout:
        title = page.title()
        raise BlockedError(
            "Listing cards did not load"
            + (" — stuck on Cloudflare challenge page" if "moment" in title.lower() else "")
            + ". Retry later or verify the URL."
        )


def _first_listing_id(page) -> str | None:
    card = page.locator(CARD_SELECTOR).first
    return card.get_attribute("da-listing-id") if card.count() else None


def _scroll_page(page) -> None:
    """Scroll through the page to trigger lazy-loaded gallery images."""
    try:
        page.evaluate(
            """async () => {
                const step = window.innerHeight;
                for (let y = 0; y <= document.body.scrollHeight; y += step) {
                    window.scrollTo(0, y);
                    await new Promise(r => setTimeout(r, 150));
                }
                window.scrollTo(0, 0);
            }"""
        )
    except Exception:
        pass  # scrolling is best-effort; JSON-LD covers missing images


def scrape(
    start_url: str = DEFAULT_START_URL,
    results_wanted: int = 20,
    max_pages: int = 10,
    delay: float = 3.0,
    headless: bool = False,
) -> tuple[list[Listing], int | None]:
    """Scrape up to results_wanted listings across max_pages pages.

    Returns (listings, total_results).
    """
    if results_wanted < 1:
        raise ValueError("results_wanted must be at least 1")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    listings: list[Listing] = []
    seen_ids: set[int] = set()
    total_results: int | None = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                locale="en-SG",
                viewport={"width": 1366, "height": 900},
            )
            context.add_init_script(STEALTH_SCRIPT)
            page = context.new_page()

            page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            _wait_for_cards(page)

            for page_number in range(1, max_pages + 1):
                _scroll_page(page)
                html = page.content()
                page_listings, page_total = parse_search_page(html, page.url, page_number)
                total_results = total_results or page_total

                new_count = 0
                for listing in page_listings:
                    if listing.listing_id in seen_ids:
                        continue
                    seen_ids.add(listing.listing_id)
                    listings.append(listing)
                    new_count += 1
                    if len(listings) >= results_wanted:
                        return listings, total_results

                if page_number == max_pages or new_count == 0:
                    break

                # Move to the next page by clicking "next" like a real user.
                next_button = page.locator(NEXT_BUTTON_SELECTOR)
                if next_button.count() == 0 or "disabled" in (
                    next_button.first.evaluate("el => el.closest('li')?.className || ''")
                ):
                    break  # last page reached

                previous_first_id = _first_listing_id(page)
                next_button.first.click()

                try:
                    page.wait_for_function(
                        """(oldId) => {
                            const card = document.querySelector(
                                'div[da-id^="parent-listing-card"]');
                            return card && card.getAttribute('da-listing-id') !== oldId;
                        }""",
                        arg=previous_first_id,
                        timeout=60000,
                    )
                except PlaywrightTimeout:
                    title = page.title()
                    if "moment" in title.lower():
                        raise BlockedError(
                            "Cloudflare challenge appeared while paginating. "
                            "Wait a while and retry."
                        )
                    break  # content did not change: assume end of results

                time.sleep(delay + random.uniform(0.0, delay / 2))
        finally:
            browser.close()

    return listings, total_results
