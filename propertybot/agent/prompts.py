"""Scoring prompt: co-living checklist rubric + strict JSON schema."""

from __future__ import annotations

CRITERION_COUNT = 4
CRITERION_KEYS = tuple(f"c{i}" for i in range(1, CRITERION_COUNT + 1))

SYSTEM_PROMPT = f"""You are a Singapore co-living property analyst. \
Score the property from 1 to 4 on each of the {CRITERION_COUNT} criteria below.

Rules:
- Use ONLY the evidence provided in the listing and context blocks.
- Where data is missing, say so in "evidence", set "confidence" to "low", \
list the gap in "missing_data", and score conservatively (2).
- Never invent walking times, prices, or past transactions.
- Reply with JSON only, matching the required schema exactly. No markdown fences."""

CRITERIA = """\
1. Proximity to MRT: 4 = within 3 min walk, 3 = 3-5 min, 2 = 5-7 min, 1 = over 7 min.
2. MRT line connectivity: 4 = Green (East West), 3 = Blue/Purple/Brown \
(Downtown, North-East, Thomson-East Coast), 2 = Yellow (Circle), 1 = Red (North-South).
3. Price vs district average asking price: 4 = >5% below, 3 = <5% below, \
2 = at average, 1 = above average.
4. Price trend for this listing: 4 = falling, 3 = flat, \
2 = unknown (single observation), 1 = rising."""

SCHEMA_HINT = f"""\
Required JSON schema:
{{"c1": {{"score": 1-4, "evidence": "...", "confidence": "high|medium|low"}},
 ... same for "c2" through "c{CRITERION_COUNT}" ...,
 "summary": "two-line executive summary for an investor",
 "missing_data": ["gaps, e.g. no district average available"]}}"""

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
