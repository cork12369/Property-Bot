"""Listing enrichment: MRT parsing, mall candidates, price context."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

LINE_COLOURS = {
    "EW": "Green (East West Line)",
    "NS": "Red (North-South Line)",
    "NE": "Purple (North-East Line)",
    "DT": "Blue (Downtown Line)",
    "TE": "Brown (Thomson-East Coast Line)",
    "CC": "Yellow (Circle Line)",
}

_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "from", "near", "singapore", "road", "street"}
)


def parse_mrt_minutes(mrt_text: str | None) -> int | None:
    if not mrt_text:
        return None
    match = re.search(r"\((\d+)\s*min", mrt_text)
    return int(match.group(1)) if match else None


def detect_mrt_lines(mrt_text: str | None) -> list[str]:
    if not mrt_text:
        return []
    seen: list[str] = []
    for code in re.findall(r"\b(EW|NS|NE|DT|TE|CC)\d*\b", mrt_text):
        if code not in seen:
            seen.append(code)
    return seen


def detect_mrt_line_colour(mrt_text: str | None) -> str | None:
    lines = detect_mrt_lines(mrt_text)
    if not lines:
        return None
    return LINE_COLOURS.get(lines[0])


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _tenant_flag(conn: sqlite3.Connection, mall: str, keywords: tuple[str, ...]) -> bool:
    clauses = " OR ".join(
        "COALESCE(subcategory, '') LIKE ? "
        "OR COALESCE(scope_of_business, '') LIKE ? "
        "OR COALESCE(store_name, '') LIKE ?"
        for _ in keywords
    )
    params: list[str] = []
    for keyword in keywords:
        pattern = f"%{keyword}%"
        params.extend([pattern, pattern, pattern])
    row = conn.execute(
        f"SELECT 1 FROM stores WHERE mall = ? AND ({clauses}) LIMIT 1",
        [mall, *params],
    ).fetchone()
    return row is not None


def find_mall_candidates(
    listing: dict, mall_conn: sqlite3.Connection, limit: int = 3
) -> tuple[list[dict], str | None]:
    try:
        mall_rows = mall_conn.execute(
            "SELECT mall_name, address, planning_region, nearest_mrt, "
            "latitude, longitude, num_stores, num_fnb FROM malls"
        ).fetchall()
    except sqlite3.Error:
        return [], "Malls table is unreadable; score criteria 2-3 with low confidence."

    listing_tokens = (
        _tokens(listing.get("district"))
        | _tokens(listing.get("street"))
        | _tokens(listing.get("address"))
        | _tokens(listing.get("mrt"))
    )
    if not listing_tokens:
        return [], "Listing has no location text; cannot match malls."

    scored: list[tuple[int, dict]] = []
    for row in mall_rows:
        mall = dict(row)
        mall_tokens = (
            _tokens(mall.get("address"))
            | _tokens(mall.get("nearest_mrt"))
            | _tokens(mall.get("planning_region"))
        )
        overlap = listing_tokens & mall_tokens
        if overlap:
            scored.append((len(overlap), mall))
    scored.sort(key=lambda item: item[0], reverse=True)

    candidates: list[dict] = []
    missing_coords = 0
    for overlap_count, mall in scored[:limit]:
        name = mall.get("mall_name")
        try:
            has_grocery = _tenant_flag(
                mall_conn, name,
                ("supermarket", "grocery", "convenience", "minimart",
                 "fairprice", "giant", "cold storage"),
            )
            has_foodcourt = _tenant_flag(
                mall_conn, name,
                ("food court", "foodcourt", "hawker", "kopitiam", "food hall"),
            )
            has_cinema = _tenant_flag(
                mall_conn, name, ("cinema", "cathay", "golden village", "shaw")
            )
            has_library = _tenant_flag(
                mall_conn, name, ("library",)
            )
        except sqlite3.Error:
            has_grocery = has_foodcourt = has_cinema = has_library = False
        lat = mall.get("latitude") or None
        lng = mall.get("longitude") or None
        if not lat or not lng:
            missing_coords += 1
        candidates.append(
            {
                "name": name,
                "address": mall.get("address"),
                "nearest_mrt": mall.get("nearest_mrt"),
                "num_stores": mall.get("num_stores"),
                "num_fnb": mall.get("num_fnb"),
                "has_grocery": has_grocery,
                "has_foodcourt": has_foodcourt,
                "has_cinema": has_cinema,
                "has_library": has_library,
                "match_reason": f"{overlap_count} location tokens overlap",
            }
        )

    note = None
    if candidates and missing_coords:
        note = (
            f"{missing_coords}/{len(candidates)} candidate malls lack coordinates; "
            "estimate walking times from addresses and mark confidence low."
        )
    elif not candidates:
        note = "No mall matched the listing location; score criteria 2-3 with low confidence."
    return candidates, note


def get_price_context(
    main_conn: sqlite3.Connection, listing: dict
) -> tuple[list[int], float | None]:
    history: list[int] = []
    if listing.get("listing_id") is not None:
        rows = main_conn.execute(
            "SELECT price_value FROM price_history WHERE listing_id = ? ORDER BY seen_at",
            (listing["listing_id"],),
        ).fetchall()
        history = [row[0] for row in rows if row[0] is not None]
    district_avg = None
    if listing.get("district"):
        row = main_conn.execute(
            "SELECT AVG(price_value) FROM listings "
            "WHERE district = ? AND price_value IS NOT NULL",
            (listing["district"],),
        ).fetchone()
        if row and row[0] is not None:
            district_avg = float(row[0])
    return history, district_avg


def build_context(
    listing: dict, main_conn: sqlite3.Connection, mall_db_path: Path
) -> dict:
    ctx: dict = {
        "mrt_minutes": parse_mrt_minutes(listing.get("mrt")),
        "mrt_line_colour": detect_mrt_line_colour(listing.get("mrt")),
        "mrt_lines": detect_mrt_lines(listing.get("mrt")),
        "malls": [],
        "mall_note": None,
        "price_history": [],
        "district_avg_price": None,
    }
    ctx["price_history"], ctx["district_avg_price"] = get_price_context(
        main_conn, listing
    )
    if not Path(mall_db_path).exists():
        ctx["mall_note"] = (
            f"Mall database not found at {mall_db_path}; "
            "score criteria 2-3 from listing text only with low confidence."
        )
        return ctx
    mall_conn = sqlite3.connect(mall_db_path)
    mall_conn.row_factory = sqlite3.Row
    try:
        ctx["malls"], ctx["mall_note"] = find_mall_candidates(listing, mall_conn)
    finally:
        mall_conn.close()
    return ctx
