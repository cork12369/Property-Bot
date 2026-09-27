"""Agent persistence: agent_runs + property_scores in propertybot.db."""

from __future__ import annotations

import json
import sqlite3

AGENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    model           TEXT NOT NULL,
    listings_seen   INTEGER DEFAULT 0,
    listings_scored INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS property_scores (
    listing_id    INTEGER NOT NULL REFERENCES listings(listing_id),
    run_id        INTEGER NOT NULL REFERENCES agent_runs(run_id),
    c1 INTEGER, c2 INTEGER, c3 INTEGER, c4 INTEGER,
    c5 INTEGER, c6 INTEGER, c7 INTEGER, c8 INTEGER,
    total         REAL,
    outcome       TEXT,
    summary       TEXT,
    evidence_json TEXT,
    scored_at     TEXT,
    PRIMARY KEY (listing_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_scores_run_total
    ON property_scores(run_id, total DESC);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(AGENT_SCHEMA)
    conn.commit()


def create_run(conn: sqlite3.Connection, model: str, started_at: str) -> int:
    cursor = conn.execute(
        "INSERT INTO agent_runs (started_at, model, status) VALUES (?, ?, 'running')",
        (started_at, model),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    seen: int,
    scored: int,
    status: str = "done",
) -> None:
    conn.execute(
        "UPDATE agent_runs SET listings_seen = ?, listings_scored = ?, status = ? "
        "WHERE run_id = ?",
        (seen, scored, status, run_id),
    )
    conn.commit()


def save_score(
    conn: sqlite3.Connection, listing_id: int, run_id: int, result: dict
) -> None:
    criteria = result["criteria"]
    conn.execute(
        "INSERT OR REPLACE INTO property_scores "
        "(listing_id, run_id, c1, c2, c3, c4, c5, c6, c7, c8, "
        " total, outcome, summary, evidence_json, scored_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            listing_id,
            run_id,
            *(criteria[f"c{i}"]["score"] for i in range(1, 9)),
            result["total"],
            result["outcome"],
            result["summary"],
            json.dumps(
                {
                    "criteria": criteria,
                    "missing_data": result.get("missing_data", []),
                },
                ensure_ascii=False,
            ),
            result.get("scored_at"),
        ),
    )
    conn.commit()


def fetch_unscored(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Listings never scored, or re-seen since their latest score."""
    query = (
        "SELECT l.* FROM listings l LEFT JOIN ("
        "  SELECT listing_id, MAX(scored_at) AS scored_at FROM property_scores"
        "  GROUP BY listing_id"
        ") s ON s.listing_id = l.listing_id "
        "WHERE s.scored_at IS NULL OR l.last_seen_at > s.scored_at "
        "ORDER BY l.listing_id"
    )
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
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


def top_n(conn: sqlite3.Connection, run_id: int, n: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT l.*, s.c1, s.c2, s.c3, s.c4, s.c5, s.c6, s.c7, s.c8, "
        "s.total, s.outcome, s.summary, s.scored_at "
        "FROM property_scores s JOIN listings l USING (listing_id) "
        "WHERE s.run_id = ? ORDER BY s.total DESC, s.listing_id LIMIT ?",
        (run_id, n),
    ).fetchall()
    return [dict(row) for row in rows]


def latest_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT run_id FROM agent_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None
