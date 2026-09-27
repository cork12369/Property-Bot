"""Command-line interface for PropertyBot."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from . import db as db_module
from . import export as export_module
from . import scraper as scraper_module
from .agent import agent_cli as agent_cli_module


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252; listings contain Unicode text."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_scrape(args: argparse.Namespace) -> int:
    print(f"Scraping: {args.url}")
    print(f"Limits: {args.max_results} results, {args.max_pages} pages, {args.delay}s delay")

    listings, total = scraper_module.scrape(
        start_url=args.url,
        results_wanted=args.max_results,
        max_pages=args.max_pages,
        delay=args.delay,
        headless=args.headless,
    )

    if total is not None:
        print(f"Total listings matching search: {total:,}")
    if not listings:
        print("No listings found.")
        return 1

    if args.dry_run:
        for listing in listings:
            print(listing.to_dict())
        print(f"\n[dry-run] Parsed {len(listings)} listings (nothing saved).")
        return 0

    conn = db_module.connect()
    try:
        summary = db_module.save_listings(conn, listings)
    finally:
        conn.close()

    print(
        f"Saved {len(listings)} listings "
        f"({summary['inserted']} new, {summary['updated']} updated, "
        f"{summary['price_changes']} price changes) to {db_module.DEFAULT_DB_PATH}"
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    conn = db_module.connect()
    try:
        path = export_module.export(conn, fmt=args.format, out=args.out)
    finally:
        conn.close()
    print(f"Exported to {path}")
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    conn = db_module.connect()
    try:
        summary = db_module.stats(conn)
    finally:
        conn.close()

    if not summary["total_listings"]:
        print("Database is empty — run a scrape first.")
        return 1

    print(f"Total listings:      {summary['total_listings']:,}")
    if summary["avg_price"] is not None:
        print(f"Average price:       S$ {summary['avg_price']:,.0f}")
    if summary["median_price"] is not None:
        print(f"Median price:        S$ {summary['median_price']:,.0f}")
    if summary["avg_psf"] is not None:
        print(f"Average psf:         S$ {summary['avg_psf']:,.2f}")
    print(f"Price history rows:  {summary['price_changes']:,}")
    print("By property type:")
    for property_type, count in summary["by_property_type"].items():
        print(f"  {property_type:<20} {count:,}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .web import app as web_app
    except ImportError as error:
        print(
            f"Error: the web GUI needs extra packages ({error}). "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    web_app.run(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="propertybot",
        description="Scrape PropertyGuru Singapore sale listings into SQLite.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="Scrape listings and save to the database")
    scrape.add_argument(
        "--url",
        default=scraper_module.DEFAULT_START_URL,
        help="PropertyGuru SG search URL (default: %(default)s)",
    )
    scrape.add_argument(
        "--max-results", type=int, default=20, help="Maximum listings to save (default: 20)"
    )
    scrape.add_argument(
        "--max-pages", type=int, default=10, help="Maximum pages to visit (default: 10)"
    )
    scrape.add_argument(
        "--delay", type=float, default=3.0, help="Delay between pages in seconds (default: 3.0)"
    )
    scrape.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser without a visible window (currently detected by "
        "Cloudflare and likely to fail — the default visible window is required)",
    )
    scrape.add_argument(
        "--dry-run", action="store_true", help="Print parsed listings without saving"
    )
    scrape.set_defaults(func=cmd_scrape)

    export = subparsers.add_parser("export", help="Export the database to JSON or CSV")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--out", default=None, help="Output file path (default: data/exports/)")
    export.set_defaults(func=cmd_export)

    stats = subparsers.add_parser("stats", help="Show summary statistics from the database")
    stats.set_defaults(func=cmd_stats)

    gui = subparsers.add_parser(
        "gui", help="Run the local web dashboard (tracks runs, properties, and evaluation)"
    )
    gui.add_argument(
        "--host", default=os.environ.get("GUI_HOST", "127.0.0.1"),
        help="Interface to bind (default: %(default)s)",
    )
    gui.add_argument(
        "--port", type=int, default=int(os.environ.get("GUI_PORT", "8000")),
        help="Port to listen on (default: %(default)s)",
    )
    gui.set_defaults(func=cmd_gui)

    agent_cli_module.add_agent_subparsers(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
