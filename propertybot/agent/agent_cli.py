"""`python main.py agent ...` subcommands: run / score-one / top / report."""

from __future__ import annotations

import argparse

from .. import db as db_module
from .config import AgentConfig, ensure_utf8_stdout
from .pipeline import run_pipeline
from . import enrich as enrich_module
from . import notify as notify_module
from . import scorer as scorer_module
from . import store as store_module
from . import openrouter_client as llm_module
from functools import partial


def cmd_agent_run(args: argparse.Namespace) -> int:
    ensure_utf8_stdout()
    config = AgentConfig.from_env()
    if args.model:
        config.openrouter_model = args.model
    result = run_pipeline(
        config,
        top_n=args.top_n,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    if result.get("dry_run"):
        print(f"\n[dry-run] Run #{result['run_id']}: prompts printed, nothing scored.")
        return 0
    if not result["scored"]:
        print("No listings scored — run a scrape first.")
        return 1
    return 0


def cmd_agent_score_one(args: argparse.Namespace) -> int:
    ensure_utf8_stdout()
    config = AgentConfig.from_env()
    if args.model:
        config.openrouter_model = args.model
    config.require_llm()
    conn = db_module.connect(config.db_path)
    try:
        store_module.ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM listings WHERE listing_id = ?", (args.listing_id,)
        ).fetchone()
        if row is None:
            print(f"No listing with id {args.listing_id}.")
            return 1
        listing = dict(row)
        from ..models import utc_now_iso

        run_id = store_module.create_run(
            conn, config.openrouter_model, utc_now_iso()
        )
        ctx = enrich_module.build_context(listing, conn, config.mall_db_path)
        chat = partial(
            llm_module.chat_json,
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
        )
        result = scorer_module.score_listing(
            listing, ctx, lambda prompt: chat(prompt)
        )
        store_module.save_score(conn, args.listing_id, run_id, result)
        store_module.finish_run(conn, run_id, seen=1, scored=1)
        print(
            f"Listing {args.listing_id}: {result['total']:.2f} "
            f"({result['outcome']}) — {result['summary']}"
        )
        return 0
    finally:
        conn.close()


def cmd_agent_top(args: argparse.Namespace) -> int:
    ensure_utf8_stdout()
    config = AgentConfig.from_env()
    conn = db_module.connect(config.db_path)
    try:
        store_module.ensure_schema(conn)
        run_id = args.run_id or store_module.latest_run_id(conn)
        if run_id is None:
            print("No agent runs yet — run `python main.py agent run` first.")
            return 1
        picks = store_module.top_n(conn, run_id, n=args.n)
        if not picks:
            print(f"Run #{run_id} has no scores yet.")
            return 1
        _, digest = notify_module.build_digest(
            picks, run_id, config.openrouter_model
        )
        print(digest)
        return 0
    finally:
        conn.close()


def cmd_agent_report(args: argparse.Namespace) -> int:
    ensure_utf8_stdout()
    config = AgentConfig.from_env()
    conn = db_module.connect(config.db_path)
    try:
        store_module.ensure_schema(conn)
        run_id = args.run_id or store_module.latest_run_id(conn)
        if run_id is None:
            print("No agent runs yet.")
            return 1
        picks = store_module.top_n(conn, run_id, n=args.n)
        run = conn.execute(
            "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        scored = run["listings_scored"] if run else len(picks)
        seen = run["listings_seen"] if run else len(picks)
        model = run["model"] if run else config.openrouter_model
        markdown = notify_module.build_report_md(picks, run_id, model, scored, seen)
        path = notify_module.write_report(config.reports_dir, run_id, markdown)
        print(f"Report written to {path}")
        return 0
    finally:
        conn.close()


def add_agent_subparsers(subparsers) -> None:
    agent = subparsers.add_parser("agent", help="LLM scoring agent harness")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    run_cmd = agent_sub.add_parser("run", help="Score unscored listings and deliver top picks")
    run_cmd.add_argument("--top-n", type=int, default=None, help="Picks to deliver (default: AGENT_TOP_N=5)")
    run_cmd.add_argument("--limit", type=int, default=None, help="Max listings to score this run")
    run_cmd.add_argument("--model", default=None, help="Override OPENROUTER_MODEL")
    run_cmd.add_argument("--dry-run", action="store_true", help="Print prompts without calling the LLM")
    run_cmd.set_defaults(func=cmd_agent_run)

    one = agent_sub.add_parser("score-one", help="Score a single listing by id")
    one.add_argument("listing_id", type=int)
    one.add_argument("--model", default=None)
    one.set_defaults(func=cmd_agent_score_one)

    top_cmd = agent_sub.add_parser("top", help="Show top picks from a run")
    top_cmd.add_argument("--n", type=int, default=5)
    top_cmd.add_argument("--run-id", type=int, default=None)
    top_cmd.set_defaults(func=cmd_agent_top)

    report = agent_sub.add_parser("report", help="Regenerate the markdown report for a run")
    report.add_argument("--run-id", type=int, default=None)
    report.add_argument("--n", type=int, default=5)
    report.set_defaults(func=cmd_agent_report)
