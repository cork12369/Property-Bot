"""Agent pipeline: select unscored listings, score via OpenRouter, deliver top picks."""

from __future__ import annotations

from functools import partial

from .. import db as db_module
from ..models import utc_now_iso
from . import enrich as enrich_module
from . import notify as notify_module
from . import openrouter_client as llm_module
from . import scorer as scorer_module
from . import store as store_module
from .config import AgentConfig
from .prompts import build_user_prompt


def run_pipeline(
    config: AgentConfig,
    *,
    top_n: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    top_n = top_n or config.top_n
    conn = db_module.connect(config.db_path)
    try:
        store_module.ensure_schema(conn)
        store_module.mark_stale_running_failed(conn)
        started_at = utc_now_iso()
        run_id = store_module.create_run(conn, config.openrouter_model, started_at)
        listings = store_module.fetch_unscored(conn, limit=limit)
        seen = len(listings)
        print(f"[agent] Run #{run_id}: {seen} listing(s) to score (model={config.openrouter_model}).")

        if dry_run:
            for listing in listings[:3]:
                ctx = enrich_module.build_context(
                    listing, conn, config.mall_db_path
                )
                prompt = build_user_prompt(listing, ctx)
                print("=" * 72)
                print(f"[dry-run] listing_id={listing.get('listing_id')}")
                print(prompt[:4000])
            store_module.finish_run(conn, run_id, seen=seen, scored=0, status="dry-run")
            return {"run_id": run_id, "seen": seen, "scored": 0, "dry_run": True}

        config.require_llm()
        chat = partial(
            llm_module.chat_json,
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
        )
        scored = 0
        for index, listing in enumerate(listings, start=1):
            listing_id = listing.get("listing_id")
            if listing_id is None:
                continue
            try:
                ctx = enrich_module.build_context(
                    listing, conn, config.mall_db_path
                )
                result = scorer_module.score_listing(
                    listing, ctx, lambda prompt: chat(prompt)
                )
                store_module.save_score(conn, listing_id, run_id, result)
                scored += 1
                print(
                    f"[agent] {index}/{seen} listing {listing_id}: "
                    f"{result['total']:.2f} ({result['outcome']})"
                )
            except Exception as error:
                print(f"[agent] Skipped listing {listing_id}: {error}")

        picks = store_module.top_n(conn, run_id, n=top_n)
        subject, digest = notify_module.build_digest(picks, run_id, config.openrouter_model)
        report_md = notify_module.build_report_md(
            picks, run_id, config.openrouter_model, scored, seen
        )
        report_path = notify_module.write_report(
            config.reports_dir, run_id, report_md
        )
        print(f"[agent] Report written to {report_path}")
        print()
        print(digest)

        notify_module.send_telegram(
            config.telegram_bot_token, config.telegram_chat_id, digest
        )
        notify_module.send_email(
            config.smtp_host, config.smtp_port, config.smtp_user,
            config.smtp_pass, config.smtp_to, config.smtp_from,
            subject, digest,
        )

        status = "done" if scored else "done-empty"
        store_module.finish_run(conn, run_id, seen=seen, scored=scored, status=status)
        return {"run_id": run_id, "seen": seen, "scored": scored, "picks": picks}
    finally:
        conn.close()
