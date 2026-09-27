"""SQLite storage: listings table, upserts, and price history."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import Listing, utc_now_iso

DEFAULT_DB_PATH = Path("data/propertybot.db")

LISTING_COLUMNS = [
    "listing_id", "title", "url", "price", "price_value", "currency",
    "price_per_area", "psf_value", "address", "street", "district",
    "bedrooms", "bathrooms", "size", "size_sqft", "property_type",
    "tenure", "build_year", "mrt", "recency", "listed_date",
    "description", "image_url", "image_urls", "image_count",
    "agent_name", "agent_company", "agent_profile_url",
    "search_url", "search_page", "first_seen_at", "last_seen_at",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id       INTEGER PRIMARY KEY,
    title            TEXT,
    url              TEXT,
    price            TEXT,
    price_value      INTEGER,
    currency         TEXT,
    price_per_area   TEXT,
    psf_value        REAL,
    address          TEXT,
    street           TEXT,
    district         TEXT,
    bedrooms         INTEGER,
    bathrooms        INTEGER,
    size             TEXT,
    size_sqft        INTEGER,
    property_type    TEXT,
    tenure           TEXT,
    build_year       INTEGER,
    mrt              TEXT,
    recency          TEXT,
    listed_date      TEXT,
    description      TEXT,
    image_url        TEXT,
    image_urls       TEXT,
    image_count      INTEGER,
    agent_name       TEXT,
    agent_company    TEXT,
    agent_profile_url TEXT,
    search_url       TEXT,
    search_page      INTEGER,
    first_seen_at    TEXT,
    last_seen_at     TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    listing_id  INTEGER NOT NULL REFERENCES listings(listing_id),
    price_value INTEGER,
    seen_at     TEXT NOT NULL,
    PRIMARY KEY (listing_id, seen_at)
);

CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
CREATE INDEX IF NOT EXISTS idx_listings_property_type ON listings(property_type);
"""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_listings(conn: sqlite3.Connection, listings: list[Listing]) -> dict:
    """Upsert listings; record price history when the price changes."""
    now = utc_now_iso()
    inserted = 0
    updated = 0
    price_changes = 0

    for listing in listings:
        if listing.listing_id is None:
            continue
        existing = conn.execute(
            "SELECT price_value, first_seen_at FROM listings WHERE listing_id = ?",
            (listing.listing_id,),
        ).fetchone()

        row = listing.to_dict()
        values = [row.get(col) for col in LISTING_COLUMNS[:-2]]
        values = [
            json.dumps(v) if col == "image_urls" else v
            for col, v in zip(LISTING_COLUMNS[:-2], values)
        ]

        if existing is None:
            conn.execute(
                f"INSERT INTO listings ({', '.join(LISTING_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(LISTING_COLUMNS))})",
                values + [now, now],
            )
            inserted += 1
            if listing.price_value is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO price_history (listing_id, price_value, seen_at) "
                    "VALUES (?, ?, ?)",
                    (listing.listing_id, listing.price_value, now),
                )
        else:
            conn.execute(
                f"UPDATE listings SET {', '.join(f'{c} = ?' for c in LISTING_COLUMNS[:-2])}, "
                "last_seen_at = ? WHERE listing_id = ?",
                values + [now, listing.listing_id],
            )
            updated += 1
            if (
                listing.price_value is not None
                and existing["price_value"] != listing.price_value
            ):
                conn.execute(
                    "INSERT OR IGNORE INTO price_history (listing_id, price_value, seen_at) "
                    "VALUES (?, ?, ?)",
                    (listing.listing_id, listing.price_value, now),
                )
                price_changes += 1

    conn.commit()
    return {"inserted": inserted, "updated": updated, "price_changes": price_changes}


def fetch_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(LISTING_COLUMNS)} FROM listings ORDER BY listing_id"
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        if item.get("image_urls"):
            try:
                item["image_urls"] = json.loads(item["image_urls"])
            except (json.JSONDecodeError, TypeError):
                item["image_urls"] = []
        results.append(item)
    return results


def stats(conn: sqlite3.Connection) -> dict:
    def one(query: str) -> float | None:
        row = conn.execute(query).fetchone()
        return row[0] if row else None

    return {
        "total_listings": one("SELECT COUNT(*) FROM listings"),
        "avg_price": one("SELECT AVG(price_value) FROM listings WHERE price_value IS NOT NULL"),
        "median_price": one(
            "SELECT price_value FROM listings WHERE price_value IS NOT NULL "
            "ORDER BY price_value LIMIT 1 OFFSET "
            "(SELECT COUNT(*) / 2 FROM listings WHERE price_value IS NOT NULL)"
        ),
        "avg_psf": one("SELECT AVG(psf_value) FROM listings WHERE psf_value IS NOT NULL"),
        "by_property_type": {
            row["property_type"] or "Unknown": row["count"]
            for row in conn.execute(
                "SELECT property_type, COUNT(*) AS count FROM listings "
                "GROUP BY property_type ORDER BY count DESC"
            ).fetchall()
        },
        "price_changes": one("SELECT COUNT(*) FROM price_history") or 0,
    }
