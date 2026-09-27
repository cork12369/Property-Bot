"""Listing data model and field normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_price(text: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    """'S$ 1,550,000' -> (1550000, 'SGD'); 'RM 650,000' -> (650000, 'MYR')."""
    if not text:
        return None, None
    currency = None
    if re.search(r"S\$|\bSGD\b", text):
        currency = "SGD"
    elif re.search(r"RM\b|\bMYR\b", text):
        currency = "MYR"
    elif "$" in text:
        currency = "SGD"
    digits = re.sub(r"[^\d]", "", text)
    return (int(digits) if digits else None), currency


def parse_int(text: Optional[str]) -> Optional[int]:
    """Extract a leading integer from text like '2', 'Built: 2018', '3+'."""
    if not text:
        return None
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group()) if match else None


def split_address(address: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'17 Mount Sophia, Orchard / River Valley' -> street, district."""
    if not address:
        return None, None
    if "," in address:
        street, district = address.rsplit(",", 1)
        return street.strip(), district.strip()
    return address, None


@dataclass
class Listing:
    listing_id: Optional[int]
    title: Optional[str] = None
    url: Optional[str] = None
    price: Optional[str] = None
    price_value: Optional[int] = None
    currency: Optional[str] = None
    price_per_area: Optional[str] = None
    psf_value: Optional[float] = None
    address: Optional[str] = None
    street: Optional[str] = None
    district: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    size: Optional[str] = None
    size_sqft: Optional[int] = None
    property_type: Optional[str] = None
    tenure: Optional[str] = None
    build_year: Optional[int] = None
    mrt: Optional[str] = None
    recency: Optional[str] = None
    listed_date: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    image_count: Optional[int] = None
    agent_name: Optional[str] = None
    agent_company: Optional[str] = None
    agent_profile_url: Optional[str] = None
    search_url: Optional[str] = None
    search_page: Optional[int] = None
    total_results: Optional[int] = None
    scraped_at: str = field(default_factory=utc_now_iso)

    def normalize(self) -> "Listing":
        self.price_value, self.currency = parse_price(self.price)
        if self.price_value is None and self.price_per_area:
            # JSON-LD sometimes only exposes numeric price via offers
            pass
        if self.price_per_area:
            match = re.search(r"[\d,]+\.?\d*", self.price_per_area)
            if match:
                self.psf_value = float(match.group().replace(",", ""))
        self.street, self.district = split_address(self.address)
        if self.bedrooms is not None and not isinstance(self.bedrooms, int):
            self.bedrooms = parse_int(str(self.bedrooms))
        if self.bathrooms is not None and not isinstance(self.bathrooms, int):
            self.bathrooms = parse_int(str(self.bathrooms))
        if self.build_year is not None and not isinstance(self.build_year, int):
            self.build_year = parse_int(str(self.build_year))
        if self.size:
            match = re.search(r"[\d,]+", self.size)
            if match:
                self.size_sqft = int(match.group().replace(",", ""))
        if self.recency:
            match = re.search(r"Listed on (.*?)(?:\s*\(|$)", self.recency)
            if match:
                self.listed_date = match.group(1).strip()
        if not self.image_count:
            self.image_count = len(self.image_urls) or None
        if not self.listing_id and self.url:
            match = re.search(r"-(\d+)$", self.url.rstrip("/"))
            if match:
                self.listing_id = int(match.group(1))
        return self

    def to_dict(self) -> dict:
        return asdict(self)
