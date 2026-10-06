"""Offline tests for the agent harness (no API keys, no network)."""

from __future__ import annotations

import io
import json
import sqlite3
import urllib.error

import pytest

from propertybot.agent import enrich, notify, openrouter_client, scorer
from propertybot.agent import store as store_module
from propertybot.agent.prompts import CRITERION_COUNT


def _sample_reply(**overrides):
    criteria = {
        f"c{i}": {"score": 3, "evidence": "test evidence", "confidence": "medium"}
        for i in range(1, CRITERION_COUNT + 1)
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
    assert result["total"] == round((4 + 3 * (CRITERION_COUNT - 1)) / CRITERION_COUNT, 3)
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
         "c1": 4, "c2": 4, "c3": 3, "c4": 2,
         "mrt": "EW4"}
    ]
    subject, digest = notify.build_digest(picks, 7, "test-model")
    assert "run #7" in subject
    assert "3.75 (GREAT)" in digest
    report = notify.build_report_md(picks, 7, "test-model", 1, 1)
    assert "property_scores" in report
    assert "c1=4" in report


def _scores(*values):
    return _sample_reply(
        **{
            f"c{i}": {"score": value, "evidence": "x", "confidence": "high"}
            for i, value in enumerate(values, start=1)
        }
    )


def test_strong_and_weak_listings_differ_by_at_least_one_point():
    strong = scorer.validate_score(_scores(4, 4, 4, 4))
    weak = scorer.validate_score(_scores(1, 1, 2, 2))
    assert strong["total"] - weak["total"] >= 1.0


def test_outcomes_are_not_degenerate():
    fixtures = [
        _scores(4, 4, 4, 4),
        _scores(4, 3, 3, 2),
        _scores(3, 3, 2, 2),
        _scores(1, 1, 2, 2),
    ]
    outcomes = {scorer.validate_score(reply)["outcome"] for reply in fixtures}
    assert len(outcomes) >= 2


def test_stale_running_agent_runs_marked_failed():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store_module.ensure_schema(conn)
    orphan = store_module.create_run(conn, "m", "2026-01-01T00:00:00+00:00")
    finished = store_module.create_run(conn, "m", "2026-01-02T00:00:00+00:00")
    store_module.finish_run(conn, finished, seen=1, scored=1)

    assert store_module.mark_stale_running_failed(conn) == 1
    statuses = {
        row["run_id"]: row["status"]
        for row in conn.execute("SELECT run_id, status FROM agent_runs")
    }
    assert statuses[orphan] == "failed"
    assert statuses[finished] == "done"


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(body.encode("utf-8"))
    )


class _OkResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(request, timeout=None):
        outcome = outcomes[min(len(calls), len(outcomes) - 1)]
        calls.append(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(openrouter_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(openrouter_client.time, "sleep", lambda _: None)
    return calls


def test_transient_provider_404_is_retried(monkeypatch):
    transient = (
        '{"error":{"message":"Provider returned error","code":404,'
        '"metadata":{"provider_name":"Meta","provider_error_code":"model_not_found"}}}'
    )
    ok = _OkResponse({"choices": [{"message": {"content": '{"a": 1}'}}]})
    calls = _patch_urlopen(monkeypatch, [_http_error(404, transient), ok])

    result = openrouter_client.chat_json("hi", api_key="k", model="m")
    assert result == {"a": 1}
    assert len(calls) == 2


def test_real_model_404_fails_fast(monkeypatch):
    calls = _patch_urlopen(
        monkeypatch, [_http_error(404, '{"error":{"message":"No such model"}}')]
    )
    with pytest.raises(openrouter_client.OpenRouterError, match="not found"):
        openrouter_client.chat_json("hi", api_key="k", model="m")
    assert len(calls) == 1


def test_privacy_guardrail_404_names_the_setting(monkeypatch):
    body = (
        '{"error":{"message":"0 endpoints out of 1 requested are available matching '
        'your guardrail restrictions and data policy. Paid model training violation"}}'
    )
    calls = _patch_urlopen(monkeypatch, [_http_error(404, body)])
    with pytest.raises(openrouter_client.OpenRouterError, match="settings/privacy"):
        openrouter_client.chat_json("hi", api_key="k", model="m")
    assert len(calls) == 1


def test_default_model_has_no_training_requirement():
    from propertybot.agent.config import AgentConfig

    assert not AgentConfig().openrouter_model.endswith("-contributor")
