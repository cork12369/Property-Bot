"""FastAPI dashboard: run history, property cards, and scrape/agent triggers."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from queue import Empty
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db as db_module
from .. import scraper as scraper_module
from ..agent import config as agent_config_module
from ..agent.prompts import CRITERIA
from ..models import utc_now_iso
from . import store as store_module
from .jobs import MAIN_PY, JobBusyError, manager

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_REPORTS_DIR = Path("data/reports")

CARD_SORTS = {
    "newest": "l.first_seen_at DESC, l.listing_id DESC",
    "price_desc": "l.price_value DESC NULLS LAST",
    "price_asc": "l.price_value ASC NULLS LAST",
    "score_desc": "s.total DESC NULLS LAST, l.listing_id DESC",
    "size_desc": "l.size_sqft DESC NULLS LAST",
}

CARD_FIELDS = """
    l.listing_id, l.title, l.url, l.price, l.price_value, l.currency,
    l.price_per_area, l.psf_value, l.address, l.street, l.district,
    l.bedrooms, l.bathrooms, l.size, l.size_sqft, l.property_type, l.tenure,
    l.build_year, l.mrt, l.recency, l.description, l.image_url, l.image_count,
    l.agent_name, l.agent_company, l.first_seen_at, l.last_seen_at,
    s.total AS score, s.outcome, s.summary, s.scored_at,
    s.c1, s.c2, s.c3, s.c4, s.c5, s.c6, s.c7, s.c8
"""

LATEST_SCORE_JOIN = """
    LEFT JOIN property_scores s
      ON s.rowid = (
            SELECT rowid FROM property_scores s2
            WHERE s2.listing_id = l.listing_id
            ORDER BY s2.scored_at DESC, s2.run_id DESC LIMIT 1
         )
"""


def _conn():
    conn = db_module.connect()
    store_module.ensure_schema(conn)
    return conn


def parse_criteria() -> list[dict]:
    """Split the prompt's numbered rubric into {key, title, description} rows."""
    entries: list[dict] = []
    for line in CRITERIA.splitlines():
        line = line.strip()
        if not line or not line[0].isdigit():
            continue
        head, _, rest = line.partition(". ")
        index = int(head)
        title, _, description = rest.partition(": ")
        entries.append(
            {
                "key": f"c{index}",
                "index": index,
                "title": title,
                "description": description,
            }
        )
    return entries


@asynccontextmanager
async def lifespan(_: FastAPI):
    conn = _conn()
    try:
        store_module.mark_stale_running_failed(conn)
    finally:
        conn.close()
    yield
    manager.close()


app = FastAPI(title="PropertyBot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/overview")
def api_overview() -> dict:
    conn = _conn()
    try:
        summary = db_module.stats(conn)
        today = date.today().isoformat()
        today_ids = store_module.scraped_since(conn, today)
        runs = store_module.list_runs(conn, limit=1)
        return {
            "stats": summary,
            "scraped_today": len(today_ids),
            "unevaluated": store_module.unevaluated_count(conn),
            "scored_total": conn.execute(
                "SELECT COUNT(DISTINCT listing_id) FROM property_scores"
            ).fetchone()[0],
            "last_run": runs[0] if runs else None,
            "today": today,
        }
    finally:
        conn.close()


@app.get("/api/properties")
def api_properties(
    q: str = "",
    district: str = "",
    property_type: str = "",
    outcome: str = "",
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    sort: str = "newest",
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    order = CARD_SORTS.get(sort, CARD_SORTS["newest"])
    where: list[str] = []
    params: list[Any] = []

    if q:
        where.append(
            "(l.title LIKE ? OR l.address LIKE ? OR l.district LIKE ? "
            "OR l.street LIKE ? OR CAST(l.listing_id AS TEXT) = ?)"
        )
        params.extend([f"%{q}%"] * 4 + [q])
    if district:
        where.append("l.district = ?")
        params.append(district)
    if property_type:
        where.append("l.property_type = ?")
        params.append(property_type)
    if outcome:
        where.append("s.outcome = ?")
        params.append(outcome)
    if min_price is not None:
        where.append("l.price_value >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("l.price_value <= ?")
        params.append(max_price)

    clause = f" WHERE {' AND '.join(where)}" if where else ""
    conn = _conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM listings l {LATEST_SCORE_JOIN}{clause}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {CARD_FIELDS} FROM listings l {LATEST_SCORE_JOIN}{clause} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        facets = {
            "districts": [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT district FROM listings "
                    "WHERE district IS NOT NULL AND district != '' ORDER BY district"
                ).fetchall()
            ],
            "property_types": [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT property_type FROM listings "
                    "WHERE property_type IS NOT NULL AND property_type != '' "
                    "ORDER BY property_type"
                ).fetchall()
            ],
        }
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "properties": [dict(row) for row in rows],
            "facets": facets,
        }
    finally:
        conn.close()


@app.get("/api/properties/{listing_id}")
def api_property(listing_id: int) -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            f"SELECT {CARD_FIELDS} FROM listings l {LATEST_SCORE_JOIN} "
            f"WHERE l.listing_id = ?",
            (listing_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Listing not found")

        listing = dict(row)
        full = conn.execute(
            "SELECT * FROM listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        listing.update(dict(full))
        if listing.get("image_urls"):
            try:
                listing["image_urls"] = json.loads(listing["image_urls"])
            except (json.JSONDecodeError, TypeError):
                listing["image_urls"] = []

        scores = conn.execute(
            "SELECT run_id, c1, c2, c3, c4, c5, c6, c7, c8, total, outcome, "
            "summary, evidence_json, scored_at FROM property_scores "
            "WHERE listing_id = ? ORDER BY scored_at DESC",
            (listing_id,),
        ).fetchall()
        history = []
        for score in scores:
            item = dict(score)
            try:
                item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["evidence"] = {}
            history.append(item)

        return {
            "listing": listing,
            "price_history": store_module.price_history(conn, listing_id),
            "scores": history,
        }
    finally:
        conn.close()


@app.get("/api/criteria")
def api_criteria() -> dict:
    return {"criteria": parse_criteria()}


@app.get("/api/days")
def api_days(days: int = Query(default=30, ge=1, le=365)) -> dict:
    conn = _conn()
    try:
        return {"days": store_module.daily_activity(conn, days=days)}
    finally:
        conn.close()


@app.get("/api/runs")
def api_runs(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    conn = _conn()
    try:
        return {"runs": store_module.list_runs(conn, limit=limit)}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def api_run(run_id: int) -> dict:
    conn = _conn()
    try:
        run = store_module.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run": run}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}/report")
def api_run_report(run_id: int) -> FileResponse:
    config = agent_config_module.AgentConfig.from_env()
    reports_dir = Path(config.reports_dir or DEFAULT_REPORTS_DIR)
    if not reports_dir.is_absolute():
        reports_dir = Path.cwd() / reports_dir
    path = reports_dir / f"agent-run-{run_id}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return FileResponse(path, media_type="text/markdown")


def _start_job(kind: str, argv: list[str], options: dict) -> dict:
    conn = _conn()
    try:
        run_id = store_module.create_run(
            conn, kind, utc_now_iso(),
            search_url=options.get("search_url"),
            max_results=options.get("max_results"),
            max_pages=options.get("max_pages"),
            headless=bool(options.get("headless")),
        )
    finally:
        conn.close()

    try:
        job = manager.start(kind, argv, run_id)
    except JobBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"job_id": job.job_id, "run_id": run_id, "job": job.snapshot()}


def _positive_int(value: Any, default: int) -> int:
    """Coerce a request field to a positive int, ignoring null/blank/zero."""
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=f"Expected a number, got {value!r}") from error
    return number if number > 0 else default


@app.post("/api/scrape")
def api_scrape(options: dict = Body(default_factory=dict)) -> dict:
    url = str(options.get("url") or scraper_module.DEFAULT_START_URL).strip()
    max_results = _positive_int(options.get("max_results"), 20)
    max_pages = _positive_int(options.get("max_pages"), 10)
    try:
        delay = float(options.get("delay") or 3.0)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="delay must be a number") from error
    headless = bool(options.get("headless"))

    argv = [
        sys.executable, str(MAIN_PY), "scrape",
        "--url", url,
        "--max-results", str(max_results),
        "--max-pages", str(max_pages),
        "--delay", str(delay),
    ]
    if headless:
        argv.append("--headless")

    return _start_job(
        "scrape", argv,
        {
            "search_url": url,
            "max_results": max_results,
            "max_pages": max_pages,
            "headless": headless,
        },
    )


@app.post("/api/agent")
def api_agent(options: dict = Body(default_factory=dict)) -> dict:
    config = agent_config_module.AgentConfig.from_env()
    top_n = _positive_int(options.get("top_n"), config.top_n or 5)
    argv = [sys.executable, str(MAIN_PY), "agent", "run", "--top-n", str(top_n)]

    limit = options.get("limit")
    if limit:
        argv += ["--limit", str(_positive_int(limit, top_n))]
    model = options.get("model") or config.openrouter_model
    argv += ["--model", str(model)]
    if options.get("dry_run"):
        argv.append("--dry-run")

    return _start_job("agent", argv, {"search_url": str(model)})


@app.get("/api/jobs/active")
def api_job_active() -> dict:
    job = manager.active()
    return {"job": job.snapshot() if job else None}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": job.snapshot(include_log=True)}


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str) -> dict:
    if not manager.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job is not running")
    return {"cancelled": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}/stream")
async def api_job_stream(job_id: str, request: Request) -> StreamingResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    queue = manager.subscribe(job)

    async def events():
        try:
            yield _sse("start", job.snapshot())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.to_thread(queue.get, True, 0.25)
                except Empty:
                    continue
                if line is None:
                    break
                yield _sse("log", {"line": line})
            yield _sse("done", job.snapshot())
        finally:
            manager.unsubscribe(job, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.exception_handler(JobBusyError)
async def _busy_handler(_: Request, error: JobBusyError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error)})


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    print(f"PropertyBot GUI: http://{host}:{port}")
    print("A Chromium window opens during scrapes (Cloudflare needs a real browser).")
    uvicorn.run(app, host=host, port=port, log_level="info")
