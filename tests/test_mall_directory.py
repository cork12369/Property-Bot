"""Smoke test: mall_directory must import and build SQLite on Python 3.10/3.11."""

from __future__ import annotations

import sqlite3

import mall_directory


def test_save_db_quotes_columns_and_inserts_rows(tmp_path):
    db_path = tmp_path / "malls.db"
    tables = {
        "malls": [{"name": "Test Mall", "latitude": "1.3", "num_stores": "5"}],
        "stores": [{"mall": "Test Mall", "store": "Shop"}],
    }
    mall_directory.save_db(db_path, tables)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM malls").fetchone()[0] == 1
        assert conn.execute("SELECT store FROM stores").fetchone()[0] == "Shop"
    finally:
        conn.close()
