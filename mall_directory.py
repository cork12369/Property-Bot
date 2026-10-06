"""Fetch the Singapore mall directory (malls + tenant stores).

Separate from the PropertyGuru scraper (propertybot/). This script does NOT
touch PropertyGuru or propertybot.db — it downloads the CC BY 4.0
`curioputterings/singapore-mall-data` dataset from GitHub and stores it in
its own CSV files and SQLite database.

Usage:
    python mall_directory.py                 # download + build SQLite
    python mall_directory.py --csv-only      # download CSVs, skip SQLite
    python mall_directory.py --force         # re-download even if files exist
    python mall_directory.py --out data/malls
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import urllib.request
from pathlib import Path

DATASET_REPO = "curioputterings/singapore-mall-data"
# Pinned to a known commit so re-runs stay reproducible until updated.
DATASET_SHA = "4845a413545993bd0b807ba2a46535f63cc27fd1"
BASE_URL = f"https://raw.githubusercontent.com/{DATASET_REPO}/{DATASET_SHA}/shopping/data"

FILES = {
    "malls": "malls.csv",
    "stores": "stores.csv",
}

DEFAULT_OUT = Path("data/mall_directory")

HEADERS = {"User-Agent": "PropertyBot-mall-directory/0.1 (+https://data.gov.sg alternative)"}

# Columns whose values are known to be numeric in this dataset.
REAL_COLUMNS = {"latitude", "longitude"}
INT_COLUMNS = {"num_stores", "num_fnb"}


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252; directory data contains Unicode."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def download_csv(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"GET {url} returned HTTP {response.status}")
        dest.write_bytes(response.read())


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def sqlite_type(column: str) -> str:
    if column in REAL_COLUMNS:
        return "REAL"
    if column in INT_COLUMNS:
        return "INTEGER"
    return "TEXT"


def save_db(db_path: Path, tables: dict[str, list[dict]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for table_name, rows in tables.items():
            if not rows:
                continue
            columns = list(rows[0].keys())
            definitions = ", ".join(f'"{col}" {sqlite_type(col)}' for col in columns)
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({definitions})")
            conn.execute(f"DELETE FROM {table_name}")
            placeholders = ", ".join("?" * len(columns))
            quoted_columns = ", ".join('"' + col + '"' for col in columns)
            conn.executemany(
                f"INSERT INTO {table_name} ({quoted_columns}) "
                f"VALUES ({placeholders})",
                [tuple(row.get(col) for col in columns) for row in rows],
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stores_mall ON stores(mall)")
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="mall_directory",
        description="Download the Singapore mall directory dataset (malls + tenants) "
        "from GitHub and store it separately from the PropertyGuru scraper.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output directory for CSV files and the SQLite database "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--csv-only",
        action="store_true",
        help="Download the CSVs without building the SQLite database",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even if they already exist",
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)

    tables: dict[str, list[dict]] = {}
    for name, filename in FILES.items():
        dest = args.out / filename
        if dest.exists() and not args.force:
            print(f"Using existing {dest}")
        else:
            url = f"{BASE_URL}/{filename}"
            print(f"Downloading {url}")
            try:
                download_csv(url, dest)
            except Exception as error:
                print(f"Error: failed to download {filename}: {error}", file=sys.stderr)
                return 1

        rows = read_csv(dest)
        tables[name] = rows
        print(f"  {filename}: {len(rows):,} rows")

    if not args.csv_only:
        db_path = args.out / "mall_directory.db"
        save_db(db_path, tables)
        print(f"Saved SQLite database to {db_path}")
    else:
        print("[csv-only] Skipped SQLite database.")

    malls = len(tables["malls"])
    stores = len(tables["stores"])
    print(f"Done: {malls:,} malls, {stores:,} tenant stores in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
