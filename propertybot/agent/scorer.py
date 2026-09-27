"""LLM score validation: strict checks, recomputed totals, derived outcomes."""

from __future__ import annotations

from typing import Callable

from ..models import utc_now_iso
from .prompts import CONFIDENCE_VALUES, build_user_prompt

CRITERION_KEYS = tuple(f"c{i}" for i in range(1, 9))


def derive_outcome(total: float) -> str:
    if total > 3.5:
        return "GREAT"
    if total > 3.0:
        return "GOOD"
    if total > 2.5:
        return "OK"
    return "FAIL"


def validate_score(raw: dict) -> dict:
    """Validate one LLM reply; recompute total/outcome from c1..c8."""
    if not isinstance(raw, dict):
        raise ValueError("LLM reply is not a JSON object.")
    criteria: dict[str, dict] = {}
    for key in CRITERION_KEYS:
        entry = raw.get(key)
        if not isinstance(entry, dict):
            raise ValueError(f"LLM reply is missing '{key}'.")
        try:
            score = int(entry.get("score"))
        except (TypeError, ValueError):
            raise ValueError(f"'{key}.score' must be an integer 1-4.") from None
        if score < 1 or score > 4:
            raise ValueError(f"'{key}.score' out of range: {score!r}.")
        confidence = str(entry.get("confidence", "low")).lower()
        if confidence not in CONFIDENCE_VALUES:
            confidence = "low"
        criteria[key] = {
            "score": score,
            "evidence": str(entry.get("evidence") or ""),
            "confidence": confidence,
        }
    total = round(sum(criteria[k]["score"] for k in CRITERION_KEYS) / 8, 3)
    missing = raw.get("missing_data") or []
    if not isinstance(missing, list):
        missing = [str(missing)]
    return {
        "criteria": criteria,
        "total": total,
        "outcome": derive_outcome(total),
        "summary": str(raw.get("summary") or ""),
        "missing_data": [str(item) for item in missing],
    }


def score_listing(
    listing: dict,
    ctx: dict,
    chat_fn: Callable[[str], dict],
) -> dict:
    """Build the prompt, call the LLM via chat_fn, validate the reply."""
    prompt = build_user_prompt(listing, ctx)
    raw = chat_fn(prompt)
    result = validate_score(raw)
    result["prompt"] = prompt
    result["scored_at"] = utc_now_iso()
    return result
