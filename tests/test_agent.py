"""Offline tests for the agent harness (no API keys, no network)."""

from __future__ import annotations

import sqlite3

from propertybot.agent import enrich, notify, scorer
from propertybot.agent import store as store_module


def _sample_reply(**overrides):
    criteria = {
        f"c{i}": {"score": 3, "evidence": "test evidence", "confidence": "medium"}
        for i in range(1, 9)
    }
    base = {
        **criteria,
        "summary": "Good co-living candidate near MRT and mall.",
        "missing_data": ["unit count"],
    }
    base.update(overrides)
    return base


def test_validate_score_recomputes_total_and_outcome():
    reply = _sample_reply(c1={"score": 4, "evidence": "3-min walk", "confidence": "high"})
    result = scorer.validate_score(reply)
    assert result["total"] == round((4 + 3 * 7) / 8, 3)
    assert result["outcome"] == "GOOD"


def test_validate_score_rejects_out_of_range():
    reply = _sample_reply(c2={"score": 9, "evidence": "x", "confidence": "high"})
    try:
        scorer.validate_score(reply)
    except ValueError:
        return
    raise AssertionError("expected ValueError for out-of-range score")


def test_derive_outcome_bands():
    assert scorer.derive_outcome(3.9) == "GREAT"
    assert scorer.derive_outcome(3.2) == "GOOD"
    assert scorer.derive_outcome(2.7) == "OK"
    assert scorer.derive_outcome(2.5) == "FAIL"


def test_mrt_parsing_and_line_detection():
    assert enrich.parse_mrt_minutes("420 m (5 min) from SW6 Layar LRT") == 5
    assert enrich.parse_mrt_minutes("no mrt info") is None
    assert enrich.detect_mrt_lines("Near EW4 Tanah Merah, CC2") == ["EW", "CC"]
    assert enrich.detect_mrt_line_colour("Next to EW4 Tanah Merah") == (
        "Green (East West Line)"
    )


def test_score_listing_uses_prompt_and_validates():
    listing = {"listing_id": 1, "title": "Test condo", "mrt": "EW4"}
    ctx = {"mrt_minutes": 5, "malls": [], "mall_note": None,
           "price_history": [], "district_avg_price": None}
    seen_prompts: list[str] = []

    def fake_chat(prompt: str) -> dict:
        seen_prompts.append(prompt)
        return _sample_reply()

    result = scorer.score_listing(listing, ctx, fake_chat)
    assert seen_prompts and "CRITERIA" in seen_prompts[0]
    assert result["total"] == 3.0
    assert result["outcome"] == "OK"


def test_store_round_trip_and_top_n():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE listings (listing_id INTEGER PRIMARY KEY, title TEXT, "
        "url TEXT, price TEXT, address TEXT, mrt TEXT, last_seen_at TEXT)"
    )
    conn.execute(
        "INSERT INTO listings VALUES (1, 'A', 'http://a', 'S$ 1M', 'X', 'EW4', 't2')"
    )
    store_module.ensure_schema(conn)
    run_id = store_module.create_run(conn, "test-model", "2026-01-01T00:00:00+00:00")
    validated = scorer.validate_score(_sample_reply())
    validated["scored_at"] = "2026-01-01T00:00:00+00:00"
    store_module.save_score(conn, 1, run_id, validated)
    picks = store_module.top_n(conn, run_id, n=5)
    assert len(picks) == 1
    assert picks[0]["total"] == validated["total"]
    assert picks[0]["title"] == "A"


def test_digest_and_report_format():
    picks = [
        {"title": "Condo A", "total": 3.75, "outcome": "GREAT",
         "price": "S$ 1,200,000", "address": "1 Test Rd",
         "summary": "Great pick.", "url": "http://example/a",
         "c1": 4, "c2": 4, "c3": 3, "c4": 4, "c5": 3, "c6": 4, "c7": 4, "c8": 2,
         "mrt": "EW4"}
    ]
    subject, digest = notify.build_digest(picks, 7, "test-model")
    assert "run #7" in subject
    assert "3.75 (GREAT)" in digest
    report = notify.build_report_md(picks, 7, "test-model", 1, 1)
    assert "property_scores" in report
    assert "c1=4" in report
