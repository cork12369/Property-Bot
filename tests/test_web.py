"""Offline tests for the web GUI store, job manager, and API routes."""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta, timezone

import pytest

from propertybot.agent import store as agent_store
from propertybot.web import jobs as jobs_module
from propertybot.web import store as web_store
from propertybot.web.app import LATEST_SCORE_JOIN, CARD_FIELDS, parse_criteria


LISTINGS_DDL = """
CREATE TABLE listings (
    listing_id INTEGER PRIMARY KEY, title TEXT, url TEXT, price TEXT,
    price_value INTEGER, currency TEXT, price_per_area TEXT, psf_value REAL,
    address TEXT, street TEXT, district TEXT, bedrooms INTEGER, bathrooms INTEGER,
    size TEXT, size_sqft INTEGER, property_type TEXT, tenure TEXT,
    build_year INTEGER, mrt TEXT, recency TEXT, listed_date TEXT, description TEXT,
    image_url TEXT, image_urls TEXT, image_count INTEGER, agent_name TEXT,
    agent_company TEXT, agent_profile_url TEXT, search_url TEXT, search_page INTEGER,
    first_seen_at TEXT, last_seen_at TEXT
);

CREATE TABLE price_history (
    listing_id INTEGER NOT NULL REFERENCES listings(listing_id),
    price_value INTEGER,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, seen_at)
);
"""


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(LISTINGS_DDL)
    web_store.ensure_schema(connection)
    agent_store.ensure_schema(connection)
    yield connection
    connection.close()


def _set_local_day(conn, run_id, iso_day):
    conn.execute("UPDATE scrape_runs SET local_day = ? WHERE run_id = ?", (iso_day, run_id))
    conn.commit()


def _add_listing(conn, listing_id, district="Orchard", price=1_200_000, first_seen="2026-01-01"):
    conn.execute(
        "INSERT INTO listings (listing_id, title, url, price, price_value, district, "
        "property_type, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (listing_id, f"Listing {listing_id}", f"http://x/{listing_id}", "S$1,200,000",
         price, district, "Condominium", first_seen, first_seen),
    )
    conn.commit()


def _add_score(conn, listing_id, run_id, total, outcome, scored_at):
    conn.execute(
        "INSERT INTO property_scores (listing_id, run_id, c1, c2, c3, c4, c5, c6, c7, c8, "
        "total, outcome, summary, evidence_json, scored_at) "
        "VALUES (?, ?, 4,3,2,4,3,2,4,3, ?, ?, 'ok', '{}', ?)",
        (listing_id, run_id, total, outcome, scored_at),
    )
    conn.commit()


# ---------------- store ----------------


def test_create_and_finish_run_round_trip(conn):
    run_id = web_store.create_run(
        conn, "scrape", "2026-05-01T09:00:00+00:00",
        search_url="http://example", max_results=20, max_pages=2, headless=False,
    )
    assert web_store.get_run(conn, run_id)["status"] == "running"

    web_store.finish_run(
        conn, run_id, finished_at="2026-05-01T09:05:00+00:00", status="done",
        exit_code=0, counters={"inserted": 4, "updated": 1, "price_changes": 2},
        log="Saved 5 listings",
    )
    run = web_store.get_run(conn, run_id)
    assert run["status"] == "done"
    assert run["inserted"] == 4
    assert run["price_changes"] == 2
    assert run["exit_code"] == 0


def test_finish_run_keeps_agent_run_id_when_absent(conn):
    run_id = web_store.create_run(conn, "agent", "2026-05-01T00:00:00+00:00")
    web_store.finish_run(
        conn, run_id, finished_at="2026-05-01T00:01:00+00:00", status="done",
        exit_code=0, counters={"agent_run_id": 7, "listings_scored": 3},
    )
    assert web_store.get_run(conn, run_id)["agent_run_id"] == 7
    assert web_store.get_run(conn, run_id)["listings_scored"] == 3


def test_daily_activity_groups_and_zero_fills(conn):
    today = date.today().isoformat()
    earlier = (date.today() - timedelta(days=2)).isoformat()

    a = web_store.create_run(conn, "scrape", f"{today}T08:00:00+00:00")
    web_store.finish_run(conn, a, finished_at=f"{today}T08:10:00+00:00",
                         status="done", exit_code=0, counters={"inserted": 3, "price_changes": 1})
    b = web_store.create_run(conn, "agent", f"{today}T09:00:00+00:00")
    web_store.finish_run(conn, b, finished_at=f"{today}T09:05:00+00:00", status="done",
                         exit_code=0, counters={"listings_scored": 2})

    c = web_store.create_run(conn, "scrape", f"{earlier}T08:00:00+00:00")
    web_store.finish_run(conn, c, finished_at=f"{earlier}T08:10:00+00:00",
                         status="failed", exit_code=1)
    # create_run stamps the day a job actually started, not the timestamp it was given
    _set_local_day(conn, c, earlier)

    activity = web_store.daily_activity(conn, days=5)
    assert len(activity) == 5
    assert activity[-1]["day"] == today
    assert activity[-1]["scrapes"] == 1
    assert activity[-1]["agent_runs"] == 1
    assert activity[-1]["inserted"] == 3
    assert activity[-1]["listings_scored"] == 2

    two_days_ago = [d for d in activity if d["day"] == earlier][0]
    assert two_days_ago["failed"] == 1
    assert two_days_ago["scrapes"] == 1

    quiet = [d for d in activity if d["day"] not in (today, earlier)]
    assert quiet and all(d["scrapes"] == 0 and d["inserted"] == 0 for d in quiet)


def test_create_run_stamps_todays_local_day(conn):
    run_id = web_store.create_run(conn, "scrape", "2020-01-01T00:00:00+00:00")
    assert web_store.get_run(conn, run_id)["local_day"] == date.today().isoformat()


def test_mark_stale_running_failed(conn):
    stale = web_store.create_run(conn, "scrape", "2026-05-01T00:00:00+00:00")
    web_store.finish_run(conn, stale, finished_at="2026-05-01T00:05:00+00:00",
                         status="done", exit_code=0)
    left_running = web_store.create_run(conn, "scrape", "2026-05-02T00:00:00+00:00")

    assert web_store.mark_stale_running_failed(conn) == 1
    assert web_store.get_run(conn, left_running)["status"] == "failed"
    assert web_store.get_run(conn, stale)["status"] == "done"


def test_scraped_since_counts_only_today(conn):
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=5)).isoformat()
    _add_listing(conn, 1, first_seen=f"{old}T08:00:00+00:00")
    _add_listing(conn, 2, first_seen=f"{today}T08:00:00+00:00")
    _add_listing(conn, 3, first_seen=f"{today}T10:00:00+00:00")
    assert web_store.scraped_since(conn, today) == {2, 3}


def test_ensure_schema_migrates_table_without_local_day(conn):
    """A DB created before local_day existed must migrate, not crash on startup."""
    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    legacy.execute(
        "CREATE TABLE scrape_runs ("
        "run_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL DEFAULT 'scrape', "
        "started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL DEFAULT 'running', "
        "search_url TEXT, max_results INTEGER, max_pages INTEGER, headless INTEGER DEFAULT 0, "
        "inserted INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, price_changes INTEGER DEFAULT 0, "
        "listings_seen INTEGER DEFAULT 0, listings_scored INTEGER DEFAULT 0, "
        "agent_run_id INTEGER, exit_code INTEGER, log TEXT)"
    )
    legacy.execute(
        "INSERT INTO scrape_runs (kind, started_at, status) "
        "VALUES ('scrape', '2026-09-27T16:20:12+00:00', 'done')"
    )
    legacy.commit()

    web_store.ensure_schema(legacy)  # must not raise

    row = web_store.get_run(legacy, 1)
    from datetime import datetime

    expected = datetime.fromisoformat("2026-09-27T16:20:12+00:00").astimezone().date().isoformat()
    assert row["local_day"] == expected
    assert row["status"] == "done"
    # the index on the new column must exist afterwards
    indexes = {r["name"] for r in legacy.execute("PRAGMA index_list(scrape_runs)")}
    assert "idx_scrape_runs_local_day" in indexes
    legacy.close()


def test_ensure_schema_is_idempotent(conn):
    web_store.ensure_schema(conn)
    web_store.ensure_schema(conn)
    run_id = web_store.create_run(conn, "scrape", "2026-01-01T00:00:00+00:00")
    assert web_store.get_run(conn, run_id)["local_day"] == date.today().isoformat()


def test_local_day_start_utc_respects_offset(conn):
    from datetime import datetime

    today = date.today().isoformat()
    cutoff = web_store.local_day_start_utc(today)
    local_midnight = datetime.fromisoformat(f"{today}T00:00:00")
    # the cutoff is local midnight expressed in UTC, so it round-trips
    assert datetime.fromisoformat(cutoff).astimezone().date() == local_midnight.date()
    assert datetime.fromisoformat(cutoff).astimezone().hour == 0

    # a timestamp just before local midnight is excluded, just after is included
    boundary = datetime.fromisoformat(cutoff)
    just_before = (boundary.astimezone(timezone.utc) - timedelta(minutes=1)).isoformat()
    just_after = (boundary.astimezone(timezone.utc) + timedelta(minutes=1)).isoformat()
    _add_listing(conn, 1, first_seen=just_before)
    _add_listing(conn, 2, first_seen=just_after)
    assert web_store.scraped_since(conn, today) == {2}


def test_daily_activity_uses_local_day_not_utc_day(conn):
    """A run at 23:00 local belongs to today even when UTC says otherwise."""
    today = date.today().isoformat()
    run_id = web_store.create_run(conn, "scrape", "2026-09-28T14:00:00+00:00")
    # UTC says 2026-09-28; the local day is what the dashboard must group by
    assert web_store.get_run(conn, run_id)["local_day"] == today
    activity = web_store.daily_activity(conn, days=3)
    assert activity[-1]["day"] == today
    assert activity[-1]["scrapes"] == 1


def test_unevaluated_count_tracks_reseen_listings(conn):
    _add_listing(conn, 1, first_seen="2026-01-01T00:00:00+00:00")
    _add_listing(conn, 2, first_seen="2026-01-01T00:00:00+00:00")
    _add_listing(conn, 3, first_seen="2026-01-01T00:00:00+00:00")
    assert web_store.unevaluated_count(conn) == 3

    _add_score(conn, 1, 1, 3.5, "GREAT", "2026-01-02T00:00:00+00:00")
    assert web_store.unevaluated_count(conn) == 2

    # re-seen after scoring -> needs re-evaluation
    conn.execute("UPDATE listings SET last_seen_at = '2026-02-01T00:00:00+00:00' WHERE listing_id = 1")
    conn.commit()
    assert web_store.unevaluated_count(conn) == 3


def test_latest_score_rows_parses_evidence(conn):
    _add_listing(conn, 1)
    _add_score(conn, 1, 1, 3.0, "OK", "2026-01-02T00:00:00+00:00")
    conn.execute(
        "UPDATE property_scores SET evidence_json = ?, scored_at = ? "
        "WHERE listing_id = 1 AND run_id = 1",
        ('{"criteria": {"c1": {"score": 4}}}', "2026-03-02T00:00:00+00:00"),
    )
    conn.commit()

    latest = web_store.latest_score_rows(conn)
    assert latest[1]["evidence"]["criteria"]["c1"]["score"] == 4


def test_card_query_joins_newest_score_without_duplicate_rows(conn):
    _add_listing(conn, 1)
    _add_score(conn, 1, 1, 2.0, "FAIL", "2026-01-02T00:00:00+00:00")
    _add_score(conn, 1, 2, 3.5, "GREAT", "2026-01-09T00:00:00+00:00")

    rows = conn.execute(
        f"SELECT {CARD_FIELDS} FROM listings l {LATEST_SCORE_JOIN} WHERE l.listing_id = 1"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "GREAT"
    assert rows[0]["score"] == 3.5
    assert rows[0]["c1"] == 4


def test_card_query_leaves_unscored_listings_null(conn):
    _add_listing(conn, 42)
    row = conn.execute(
        f"SELECT {CARD_FIELDS} FROM listings l {LATEST_SCORE_JOIN} WHERE l.listing_id = 42"
    ).fetchone()
    assert row["listing_id"] == 42
    assert row["score"] is None and row["outcome"] is None


def _endpoint_conn(conn, monkeypatch):
    """Route the API endpoints at the in-memory fixture database."""
    from propertybot.web import app as web_app

    class _KeepOpen:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass  # endpoints must not close the shared fixture connection

    wrapped = _KeepOpen(conn)
    monkeypatch.setattr(web_app, "_conn", lambda: wrapped)
    return web_app


def test_card_query_includes_first_price(conn):
    _add_listing(conn, 7, price=1_350_000)
    conn.execute(
        "INSERT INTO price_history (listing_id, price_value, seen_at) VALUES (7, 1300000, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO price_history (listing_id, price_value, seen_at) VALUES (7, 1350000, '2026-02-01')"
    )
    conn.commit()
    row = conn.execute(
        f"SELECT {CARD_FIELDS} FROM listings l {LATEST_SCORE_JOIN} WHERE l.listing_id = 7"
    ).fetchone()
    assert row["first_price"] == 1_300_000


def test_properties_endpoint_outcomes_facet_and_unscored_filter(conn, monkeypatch):
    _add_listing(conn, 1)
    _add_listing(conn, 2)
    _add_score(conn, 1, 1, 3.75, "GREAT", "2026-01-02T00:00:00+00:00")
    web_app = _endpoint_conn(conn, monkeypatch)

    data = web_app.api_properties(limit=24, offset=0)
    assert data["facets"]["outcomes"] == {"GREAT": 1, "unscored": 1}

    unscored = web_app.api_properties(outcome="unscored", limit=24, offset=0)
    assert unscored["total"] == 1
    assert unscored["properties"][0]["listing_id"] == 2

    great = web_app.api_properties(outcome="GREAT", limit=24, offset=0)
    assert great["total"] == 1
    assert great["properties"][0]["listing_id"] == 1


# ---------------- jobs ----------------


def test_extract_counters_parses_scrape_summary():
    job = jobs_module.Job("j1", "scrape", 1, ["python", "main.py", "scrape"])
    job.lines.extend([
        "Scraping: http://example",
        "Total listings matching search: 1,234",
        "Saved 20 listings (17 new, 3 updated, 1 price changes) to data/propertybot.db",
    ])
    counters = job.extract_counters()
    assert counters == {"inserted": 17, "updated": 3, "price_changes": 1}


def test_extract_counters_parses_agent_output():
    job = jobs_module.Job("j2", "agent", 2, ["python", "main.py", "agent", "run"])
    job.lines.extend([
        "[agent] Run #12: 3 listing(s) to score (model=test).",
        "[agent] 1/3 listing 10: 3.00 (OK)",
        "[agent] 2/3 listing 11: 3.50 (GREAT)",
        "[agent] 3/3 listing 12: 3.25 (GOOD)",
    ])
    counters = job.extract_counters()
    assert counters["agent_run_id"] == 12
    assert counters["listings_seen"] == 3
    assert counters["listings_scored"] == 3


def test_extract_counters_is_empty_for_error_output():
    job = jobs_module.Job("j3", "scrape", 3, [])
    job.lines.extend(["Error: Listing cards did not load - stuck on Cloudflare challenge page."])
    assert job.extract_counters() == {}


def test_job_busy_rejects_second_job():
    manager = jobs_module.JobManager()
    job = jobs_module.Job("busy", "scrape", 1, [])
    manager._jobs[job.job_id] = job
    manager._active = job
    with pytest.raises(jobs_module.JobBusyError):
        manager.start("agent", ["python"], 2)


def test_subscribe_replays_buffer_and_signals_finished_job():
    manager = jobs_module.JobManager()
    job = jobs_module.Job("j4", "scrape", 1, [])
    job.lines.extend(["line one", "line two"])
    job.status = "done"

    queue = manager.subscribe(job)
    assert queue.get_nowait() == "line one"
    assert queue.get_nowait() == "line two"
    assert queue.get_nowait() is None


def test_child_env_sets_utf8():
    assert jobs_module.child_env()["PYTHONIOENCODING"] == "utf-8"


# ---------------- misc ----------------


def test_parse_criteria_matches_rubric_size():
    from propertybot.agent.prompts import CRITERION_COUNT

    criteria = parse_criteria()
    assert len(criteria) == CRITERION_COUNT
    assert criteria[0]["key"] == "c1"
    assert "MRT" in criteria[0]["title"]
    assert criteria[-1]["key"] == f"c{CRITERION_COUNT}"
    assert all(row["description"] for row in criteria)


def test_conn_creates_agent_schema_on_fresh_db(tmp_path, monkeypatch):
    from propertybot import db as db_module
    from propertybot.web import app as web_app

    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", tmp_path / "fresh.db")
    overview = web_app.api_overview()
    assert overview["scored_total"] == 0
    assert overview["unevaluated"] == 0


def test_backfill_local_day_uses_local_date(monkeypatch):
    from datetime import datetime

    legacy = sqlite3.connect(":memory:")
    legacy.row_factory = sqlite3.Row
    legacy.execute(
        "CREATE TABLE scrape_runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "kind TEXT NOT NULL DEFAULT 'scrape', started_at TEXT NOT NULL, finished_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'running', search_url TEXT, max_results INTEGER, "
        "max_pages INTEGER, headless INTEGER DEFAULT 0, inserted INTEGER DEFAULT 0, "
        "updated INTEGER DEFAULT 0, price_changes INTEGER DEFAULT 0, "
        "listings_seen INTEGER DEFAULT 0, listings_scored INTEGER DEFAULT 0, "
        "agent_run_id INTEGER, exit_code INTEGER, log TEXT)"
    )
    started = "2026-09-27T16:20:12+00:00"
    legacy.execute("INSERT INTO scrape_runs (started_at) VALUES (?)", (started,))
    legacy.commit()

    web_store.ensure_schema(legacy)
    expected = datetime.fromisoformat(started).astimezone().date().isoformat()
    assert web_store.get_run(legacy, 1)["local_day"] == expected
    legacy.close()


def test_headless_flag_fails_fast_with_xvfb_hint():
    from propertybot.cli import main

    assert main(["scrape", "--headless"]) == 1


def test_positive_int_falls_back_and_rejects_garbage():
    from fastapi import HTTPException

    from propertybot.web.app import _positive_int

    assert _positive_int(None, 20) == 20
    assert _positive_int("", 20) == 20
    assert _positive_int(0, 20) == 20  # 0 is falsy, must not become max-results 0
    assert _positive_int(-5, 20) == 20
    assert _positive_int("7", 20) == 7
    assert _positive_int(7, 20) == 7
    for bad in ("abc", [], {}, 1.5j):
        try:
            _positive_int(bad, 20)
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError(f"expected HTTPException for {bad!r}")


def test_scrape_endpoint_builds_argv_without_starting_a_browser(monkeypatch):
    from propertybot.web import app as web_app

    captured = {}

    def fake_start(kind, argv, options):
        captured["kind"] = kind
        captured["argv"] = argv
        captured["options"] = options
        return {"job_id": "fake", "run_id": 1, "job": {"kind": kind}}

    monkeypatch.setattr(web_app, "_start_job", fake_start)
    result = web_app.api_scrape({"max_results": 0, "max_pages": None, "delay": None})

    assert result["job"]["kind"] == "scrape"
    argv = captured["argv"]
    assert "--max-results" in argv and argv[argv.index("--max-results") + 1] == "20"
    assert argv[argv.index("--max-pages") + 1] == "10"
    assert argv[argv.index("--delay") + 1] == "3.0"
    assert "--headless" not in argv
    assert captured["options"]["search_url"].startswith("https://www.propertyguru.com.sg")


def test_scrape_endpoint_passes_headless_flag(monkeypatch):
    from propertybot.web import app as web_app

    captured = {}

    def fake_start(kind, argv, options):
        captured["argv"] = argv
        return {"job_id": "fake", "run_id": 1, "job": {"kind": kind}}

    monkeypatch.setattr(web_app, "_start_job", fake_start)
    web_app.api_scrape({"headless": True, "max_results": 5, "max_pages": 2})
    assert "--headless" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--max-results") + 1] == "5"


def test_gui_subcommand_is_registered():
    from propertybot.cli import build_parser

    args = build_parser().parse_args(["gui", "--port", "9123"])
    assert args.func.__name__ == "cmd_gui"
    assert args.port == 9123


def test_main_py_runs_as_gui_command():
    import subprocess

    result = subprocess.run(
        [sys.executable, "main.py", "gui", "--help"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0
    assert "--port" in result.stdout
