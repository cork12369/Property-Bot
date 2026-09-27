"""Parse PropertyGuru search-result HTML into Listing objects."""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .models import Listing, parse_int

CARD_SELECTOR = 'div[da-id^="parent-listing-card"]'


def _text(node: Optional[Tag]) -> Optional[str]:
    if node is None:
        return None
    text = node.get_text(" ", strip=True)
    return text or None


def _text_in(card: Tag, attr: str) -> Optional[str]:
    return _text(card.find(attrs={"da-id": attr}))


def _feature_value(card: Tag, attr: str) -> Optional[str]:
    """Value inside e.g. <div da-id="listing-card-v2-bedrooms"><p>2</p></div>."""
    node = card.find(attrs={"da-id": attr})
    if node is None:
        return None
    return _text(node.find("p")) or _text(node)


def _attr_value(card: Tag, attr: str, attribute: str) -> Optional[str]:
    node = card.find(attrs={"da-id": attr})
    if node is None:
        return None
    value = node.get(attribute)
    return value if isinstance(value, str) else None


def _absolute(base_url: str, href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    return urljoin(base_url, href)


def _parse_images(card: Tag) -> list[str]:
    urls = []
    for img in card.select(".gallery img[src]"):
        src = img.get("src", "")
        # Skip lazy-load placeholders and UI icons (chevrons, fallbacks)
        if (
            not src
            or "image-fallback" in src
            or "hive-ui" in src
            or src in urls
        ):
            continue
        urls.append(src)
    return urls


def _image_count(card: Tag) -> Optional[int]:
    btn = card.find(attrs={"da-id": "listing-card-v2-images-info-btn"})
    if btn is not None:
        title = btn.get("title", "")
        if isinstance(title, str):
            match = re.search(r"(\d+)", title)
            if match:
                return int(match.group(1))
    return None


def _parse_card(card: Tag, base_url: str) -> Optional[Listing]:
    raw_id = card.get("da-listing-id")
    listing_id = int(raw_id) if isinstance(raw_id, str) and raw_id.isdigit() else None

    link = card.select_one("a.card-footer") or card.select_one(".gallery a[href]")
    url = _absolute(base_url, link.get("href")) if link else None
    if listing_id is None and url:
        match = re.search(r"-(\d+)$", url.rstrip("/"))
        if match:
            listing_id = int(match.group(1))
    if listing_id is None and url is None:
        return None

    profile_link = card.select_one(".profile-link")
    description_node = card.select_one('[da-id="listing-card-v2-headline"] .agent-description')

    return Listing(
        listing_id=listing_id,
        title=_text_in(card, "listing-card-v2-title"),
        url=url,
        price=_text_in(card, "listing-card-v2-price"),
        price_per_area=_text_in(card, "listing-card-v2-psf"),
        address=_text(card.select_one(".listing-address")),
        bedrooms=parse_int(_feature_value(card, "listing-card-v2-bedrooms")),
        bathrooms=parse_int(_feature_value(card, "listing-card-v2-bathrooms")),
        size=_feature_value(card, "listing-card-v2-area"),
        property_type=_feature_value(card, "listing-card-v2-unit-type"),
        tenure=_feature_value(card, "listing-card-v2-tenure"),
        build_year=parse_int(_feature_value(card, "listing-card-v2-build-year")),
        mrt=_text(card.select_one('[da-id="listing-card-v2-mrt"] .listing-location-value')),
        recency=_text(card.select_one('[da-id="listing-card-v2-recency"]')),
        description=_text(description_node),
        image_urls=_parse_images(card),
        image_count=_image_count(card),
        agent_name=_text_in(card, "listing-card-v2-agent-name"),
        agent_company=_text_in(card, "listing-card-v2-agency-name"),
        agent_profile_url=_absolute(
            base_url, profile_link.get("href") if profile_link else None
        ),
    )


def _parse_jsonld(soup: BeautifulSoup) -> dict[int, dict]:
    """Index JSON-LD ItemList entries by listing id extracted from their URL."""
    index: dict[int, dict] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in graph:
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "ItemList":
                for element in node.get("itemListElement", []):
                    item = element.get("item", {})
                    url = item.get("url", "")
                    match = re.search(r"-(\d+)$", url.rstrip("/"))
                    if not match:
                        continue
                    raw_props = item.get("additionalProperty", [])
                    props = raw_props if isinstance(raw_props, list) else [raw_props]
                    index[int(match.group(1))] = {
                        "title": item.get("name"),
                        "url": url,
                        "image": item.get("image"),
                        "price": item.get("offers", {}).get("price"),
                        "currency": item.get("offers", {}).get("priceCurrency"),
                        "property_type": next(
                            (
                                prop.get("value")
                                for prop in props
                                if isinstance(prop, dict)
                                and prop.get("name") == "Property Type"
                            ),
                            None,
                        ),
                    }
    return index


def _apply_jsonld_fallback(listing: Listing, entry: Optional[dict]) -> None:
    if not entry:
        return
    if listing.price is None and entry.get("price") is not None:
        listing.price_value = int(entry["price"])
        listing.currency = entry.get("currency")
    if listing.image_url is None:
        listing.image_url = entry.get("image")
    if not listing.image_urls and entry.get("image"):
        listing.image_urls = [entry["image"]]
    if listing.property_type is None:
        listing.property_type = entry.get("property_type")


def parse_search_page(html: str, page_url: str, page_number: int) -> tuple[list[Listing], Optional[int]]:
    """Parse one search-results page. Returns (listings, total_results)."""
    soup = BeautifulSoup(html, "lxml")

    cards = soup.select(CARD_SELECTOR)
    jsonld = _parse_jsonld(soup)

    # A page with neither cards nor JSON-LD items is almost certainly a block page.
    if not cards and not jsonld:
        raise ValueError(
            "No listing cards or structured data found on the page — "
            "the request was likely blocked or the URL is invalid. "
            "Increase --delay or verify the URL."
        )

    total_results: Optional[int] = None
    h1 = soup.select_one("h1.page-title")
    if h1:
        match = re.search(r"([\d,]+)", h1.get_text())
        if match:
            total_results = int(match.group(1).replace(",", ""))

    listings: list[Listing] = []
    for card in cards:
        listing = _parse_card(card, page_url)
        if listing is None:
            continue
        listing.search_url = page_url
        listing.search_page = page_number
        listing.total_results = total_results
        _apply_jsonld_fallback(listing, jsonld.get(listing.listing_id))
        listings.append(listing.normalize())

    return listings, total_results
