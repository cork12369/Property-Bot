"""Web GUI run history: scrape_runs table plus day-level activity rollups."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

WEB_SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL DEFAULT 'scrape',
    started_at      TEXT NOT NULL,
    local_day       TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    search_url      TEXT,
    max_results     INTEGER,
    max_pages       INTEGER,
    headless        INTEGER DEFAULT 0,
    inserted        INTEGER DEFAULT 0,
    updated         INTEGER DEFAULT 0,
    price_changes   INTEGER DEFAULT 0,
    listings_seen   INTEGER DEFAULT 0,
    listings_scored INTEGER DEFAULT 0,
    agent_run_id    INTEGER,
    exit_code       INTEGER,
    log             TEXT
);
"""

INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_scrape_runs_started ON scrape_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_local_day ON scrape_runs(local_day DESC);
"""

RUN_COLUMNS = [
    "run_id", "kind", "started_at", "local_day", "finished_at", "status",
    "search_url", "max_results", "max_pages", "headless", "inserted", "updated",
    "price_changes", "listings_seen", "listings_scored", "agent_run_id",
    "exit_code", "log",
]


def local_day_start_utc(iso_day: str) -> str:
    """UTC cutoff for the local calendar day, e.g. '2026-09-28'."""
    local_midnight = datetime.fromisoformat(f"{iso_day}T00:00:00")
    return local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(WEB_SCHEMA)
    _add_missing_columns(conn)
    conn.executescript(INDEX_SCHEMA)
    conn.commit()


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Backfill local_day on databases written before the column existed."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(scrape_runs)")}
    if "local_day" in columns:
        return
    conn.execute("ALTER TABLE scrape_runs ADD COLUMN local_day TEXT")
    for row in conn.execute("SELECT run_id, started_at FROM scrape_runs").fetchall():
        started = datetime.fromisoformat(row["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        conn.execute(
            "UPDATE scrape_runs SET local_day = ? WHERE run_id = ?",
            (started.astimezone().date().isoformat(), row["run_id"]),
        )


def create_run(
    conn: sqlite3.Connection,
    kind: str,
    started_at: str,
    *,
    search_url: Optional[str] = None,
    max_results: Optional[int] = None,
    max_pages: Optional[int] = None,
    headless: bool = False,
) -> int:
    cursor = conn.execute(
        "INSERT INTO scrape_runs "
        "(kind, started_at, local_day, status, search_url, max_results, max_pages, headless) "
        "VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
        (
            kind, started_at, date.today().isoformat(),
            search_url, max_results, max_pages, int(headless),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    finished_at: str,
    status: str,
    exit_code: Optional[int] = None,
    counters: Optional[dict] = None,
    log: str = "",
) -> None:
    counters = counters or {}
    conn.execute(
        "UPDATE scrape_runs SET finished_at = ?, status = ?, exit_code = ?, "
        "inserted = ?, updated = ?, price_changes = ?, "
        "listings_seen = ?, listings_scored = ?, agent_run_id = COALESCE(?, agent_run_id), "
        "log = ? WHERE run_id = ?",
        (
            finished_at,
            status,
            exit_code,
            counters.get("inserted", 0),
            counters.get("updated", 0),
            counters.get("price_changes", 0),
            counters.get("listings_seen", 0),
            counters.get("listings_scored", 0),
            counters.get("agent_run_id"),
            log,
            run_id,
        ),
    )
    conn.commit()


def list_runs(conn: sqlite3.Connection, limit: int = 50, kind: Optional[str] = None) -> list[dict]:
    query = f"SELECT {', '.join(RUN_COLUMNS)} FROM scrape_runs"
    params: list[Any] = []
    if kind:
        query += " WHERE kind = ?"
        params.append(kind)
    query += " ORDER BY run_id DESC LIMIT ?"
    params.append(int(limit))
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    row = conn.execute(
        f"SELECT {', '.join(RUN_COLUMNS)} FROM scrape_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


def mark_stale_running_failed(conn: sqlite3.Connection) -> int:
    """Runs left 'running' by a previous server process can never finish."""
    cursor = conn.execute(
        "UPDATE scrape_runs SET status = 'failed', finished_at = started_at "
        "WHERE status = 'running'"
    )
    conn.commit()
    return cursor.rowcount


def daily_activity(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """Per-day scrape/agent counts, oldest first, with empty days zero-filled."""
    days = max(1, min(int(days), 365))
    today = date.today()
    span = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    first_day = span[0].isoformat()

    rows = conn.execute(
        "SELECT local_day AS day, "
        "  SUM(kind = 'scrape') AS scrapes, "
        "  SUM(kind = 'agent') AS agent_runs, "
        "  SUM(status = 'failed') AS failed, "
        "  COALESCE(SUM(inserted), 0) AS inserted, "
        "  COALESCE(SUM(price_changes), 0) AS price_changes, "
        "  COALESCE(SUM(listings_seen), 0) AS listings_seen, "
        "  COALESCE(SUM(listings_scored), 0) AS listings_scored "
        "FROM scrape_runs WHERE local_day >= ? GROUP BY local_day",
        (first_day,),
    ).fetchall()

    by_day = {row["day"]: dict(row) for row in rows}
    activity = []
    for day in span:
        key = day.isoformat()
        row = by_day.get(key)
        activity.append(
            {
                "day": key,
                "scrapes": int(row["scrapes"]) if row else 0,
                "agent_runs": int(row["agent_runs"]) if row else 0,
                "failed": int(row["failed"]) if row else 0,
                "inserted": int(row["inserted"]) if row else 0,
                "price_changes": int(row["price_changes"]) if row else 0,
                "listings_seen": int(row["listings_seen"]) if row else 0,
                "listings_scored": int(row["listings_scored"]) if row else 0,
            }
        )
    return activity


def scraped_since(conn: sqlite3.Connection, iso_day: str) -> set[int]:
    """Listing ids first seen within the given local YYYY-MM-DD day."""
    rows = conn.execute(
        "SELECT listing_id FROM listings WHERE first_seen_at >= ?",
        (local_day_start_utc(iso_day),),
    ).fetchall()
    return {int(row[0]) for row in rows if row[0] is not None}


def latest_score_rows(conn: sqlite3.Connection) -> dict[int, dict]:
    """Newest score per listing, keyed by listing_id."""
    rows = conn.execute(
        "SELECT s.* FROM property_scores s JOIN ("
        "  SELECT listing_id, MAX(scored_at) AS scored_at FROM property_scores "
        "  GROUP BY listing_id"
        ") m ON m.listing_id = s.listing_id AND m.scored_at = s.scored_at"
    ).fetchall()
    results: dict[int, dict] = {}
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["evidence"] = {}
        results[int(item["listing_id"])] = item
    return results


def unevaluated_count(conn: sqlite3.Connection) -> int:
    """Listings with no score, or re-seen since their newest score."""
    row = conn.execute(
        "SELECT COUNT(*) FROM listings l LEFT JOIN ("
        "  SELECT listing_id, MAX(scored_at) AS scored_at FROM property_scores "
        "  GROUP BY listing_id"
        ") s ON s.listing_id = l.listing_id "
        "WHERE s.scored_at IS NULL OR l.last_seen_at > s.scored_at"
    ).fetchone()
    return int(row[0]) if row else 0


def price_history(conn: sqlite3.Connection, listing_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT price_value, seen_at FROM price_history "
        "WHERE listing_id = ? ORDER BY seen_at",
        (listing_id,),
    ).fetchall()
    return [dict(row) for row in rows]
