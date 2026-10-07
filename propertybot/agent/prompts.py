"""Scoring prompt: co-living checklist rubric + strict JSON schema."""

from __future__ import annotations

CRITERION_COUNT = 8
CRITERION_KEYS = tuple(f"c{i}" for i in range(1, CRITERION_COUNT + 1))
MISSING_DATA_SCORE = 1

SYSTEM_PROMPT = f"""You are a Singapore co-living property analyst. \
Score the property from 1 to 4 on each of the {CRITERION_COUNT} criteria below.

Rules:
- Use ONLY the evidence provided in the listing and context blocks.
- Where the data needed for a criterion is missing or unresolvable, say so in \
"evidence", set "confidence" to "low", list the gap in "missing_data", and score {MISSING_DATA_SCORE} (weakest).
- Only a transient mall-coordinate gap may be estimated from addresses, and then \
with "confidence" "low".
- Never invent unit counts, facilities, walking times, or past transactions.
- Reply with JSON only, matching the required schema exactly. No markdown fences."""

CRITERIA = """\
1. Proximity to MRT Lines: 4 = walk within 3 min, 3 = 3-5 min, 2 = 5-7 min, 1 = over 7 min.
2. Proximity to shopping mall: 4 = walk within 3 min, 3 = 3-5 min, 2 = 5-7 min, 1 = over 7 min.
3. Tenant mix in mall: 4 = cinema + library + grocery + food court, \
3 = small mall with grocery + food court, 2 = big neighbourhood retailers, \
1 = small neighbourhood retailers.
4. Number of units in the project: 4 = 500-700, 3 = 700-1000, \
2 = 300-500 or 1000-1300, 1 = under 300 or over 1300.
5. Availability of facilities: 4 = tennis court + gym + lap pool, 3 = gym + lap pool, \
2 = gym or pool only, 1 = no facilities.
6. Rental demand in the area: 4 = very strong (within business parks and schools), \
3 = strong (within business park or universities), 2 = normal \
(ripple effect from business park and universities), 1 = weak (no strong demand).
7. MRT Lines: 4 = Green (East West), 3 = Blue/Purple/Brown \
(Downtown, North-East, Thomson-East Coast), 2 = Yellow (Circle), 1 = Red (North-South).
8. Closing price vs past transactions: 4 = >5% below past average transaction, \
3 = <5% below past average, 2 = according to past average, 1 = above past average. \
Compare against past transaction prices when available; otherwise the district \
average asking price is a weak proxy — say which benchmark you used in "evidence" \
and set "confidence" to "low" when it is the proxy."""

SCHEMA_HINT = f"""\
Required JSON schema:
{{"c1": {{"score": 1-4, "evidence": "...", "confidence": "high|medium|low"}},
 ... same for "c2" through "c{CRITERION_COUNT}" ...,
 "summary": "two-line executive summary for an investor",
 "missing_data": ["gaps, e.g. unit count not listed"]}}"""

CONFIDENCE_VALUES = ("high", "medium", "low")


def _listing_block(listing: dict) -> str:
    fields = (
        "listing_id", "title", "url", "price", "price_per_area", "address",
        "bedrooms", "bathrooms", "size", "property_type", "tenure",
        "build_year", "mrt", "description",
    )
    lines = [
        f"{name}: {listing.get(name)}"
        for name in fields
        if listing.get(name) not in (None, "", [])
    ]
    return "\n".join(lines)


def _context_block(ctx: dict) -> str:
    lines = []
    if ctx.get("mrt_minutes") is not None:
        lines.append(f"Parsed MRT walk time: ~{ctx['mrt_minutes']} min.")
    if ctx.get("mrt_line_colour"):
        lines.append(f"Detected MRT line colour: {ctx['mrt_line_colour']}.")
    for mall in ctx.get("malls", []):
        lines.append(
            f"Mall candidate: {mall.get('name')} | {mall.get('address')} | "
            f"nearest MRT: {mall.get('nearest_mrt')} | stores: "
            f"{mall.get('num_stores')} ({mall.get('num_fnb')} F&B) | "
            f"grocery={mall.get('has_grocery')} foodcourt={mall.get('has_foodcourt')} "
            f"cinema={mall.get('has_cinema')} library={mall.get('has_library')} "
            f"({mall.get('match_reason')})"
        )
    if ctx.get("mall_note"):
        lines.append(f"Mall data note: {ctx['mall_note']}")
    history = ctx.get("price_history") or []
    if history:
        lines.append(f"Price history (oldest to newest): {history}.")
    if ctx.get("district_avg_price"):
        lines.append(
            f"District average asking price: S$ {ctx['district_avg_price']:,.0f}."
        )
    return "\n".join(lines) if lines else "(no enrichment available)"


def build_user_prompt(listing: dict, ctx: dict) -> str:
    return (
        "LISTING\n" + _listing_block(listing)
        + "\n\nENRICHED CONTEXT\n" + _context_block(ctx)
        + "\n\nCRITERIA\n" + CRITERIA
        + "\n\n" + SCHEMA_HINT
    )
