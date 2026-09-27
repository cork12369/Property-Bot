"""Export listings from the database to JSON or CSV files."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .db import fetch_all, LISTING_COLUMNS

EXPORTS_DIR = Path("data/exports")


def export(conn, fmt: str = "json", out: str | None = None) -> Path:
    rows = fetch_all(conn)
    if not rows:
        raise ValueError("Database is empty — run a scrape first.")

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    default_name = f"listings-{timestamp}.{fmt}"
    path = Path(out) if out else EXPORTS_DIR / default_name
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        with open(path, "w", encoding="utf-8", newline="") as file:
            json.dump(rows, file, ensure_ascii=False, indent=2)
    elif fmt == "csv":
        columns = [col for col in LISTING_COLUMNS]
        with open(path, "w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                if isinstance(row.get("image_urls"), list):
                    row["image_urls"] = "; ".join(row["image_urls"])
                writer.writerow(row)
    else:
        raise ValueError(f"Unsupported format: {fmt} (use 'json' or 'csv')")

    return path
