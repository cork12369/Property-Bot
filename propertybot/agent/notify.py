"""Delivery: Telegram + Email + local markdown report (fail-open notifiers)."""

from __future__ import annotations

import json
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


def format_pick_card(pick: dict, rank: int) -> str:
    return (
        f"{rank}. {pick.get('title') or 'Untitled'} — "
        f"{pick.get('total'):.2f} ({pick.get('outcome')})\n"
        f"   {pick.get('price') or 'price n/a'} | "
        f"{pick.get('address') or 'address n/a'}\n"
        f"   {pick.get('summary') or ''}\n"
        f"   {pick.get('url') or ''}"
    )


def build_digest(picks: list[dict], run_id: int, model: str) -> tuple[str, str]:
    subject = f"PropertyBot top {len(picks)} co-living picks (run #{run_id})"
    lines = [
        subject,
        f"Model: {model}",
        "",
        "Top properties by co-living score (total/8):",
        "",
    ]
    for rank, pick in enumerate(picks, start=1):
        lines.append(format_pick_card(pick, rank))
        lines.append("")
    lines.append(f"Full report: data/reports/agent-run-{run_id}.md")
    body = "\n".join(lines).strip()
    return subject, body


def build_report_md(
    picks: list[dict], run_id: int, model: str, scored: int, seen: int
) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# PropertyBot agent report — run #{run_id}",
        "",
        f"Generated: {stamp} | Model: `{model}` | "
        f"Scored {scored}/{seen} listings in this run.",
        "",
        "GUI contract: scores live in `propertybot.db → property_scores` "
        "(columns: listing_id, run_id, c1..c8, total, outcome, summary, "
        "evidence_json, scored_at); runs in `agent_runs`.",
        "",
        "## Top picks",
        "",
    ]
    for rank, pick in enumerate(picks, start=1):
        lines.append(
            f"### {rank}. {pick.get('title') or 'Untitled'} — "
            f"{pick.get('total'):.2f} ({pick.get('outcome')})"
        )
        lines.append("")
        lines.append(f"- Price: {pick.get('price') or 'n/a'}")
        lines.append(f"- Address: {pick.get('address') or 'n/a'}")
        lines.append(f"- MRT: {pick.get('mrt') or 'n/a'}")
        lines.append(f"- Link: {pick.get('url') or 'n/a'}")
        lines.append("")
        lines.append(str(pick.get("summary") or ""))
        lines.append("")
        breakdown = ", ".join(
            f"c{i}={pick.get(f'c{i}')}" for i in range(1, 9)
        )
        lines.append(f"Score breakdown: {breakdown}")
        lines.append("")
    return "\n".join(lines)


def write_report(reports_dir: Path, run_id: int, markdown: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"agent-run-{run_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    if not bot_token or not chat_id:
        print("[notify] Telegram skipped: TELEGRAM_BOT_TOKEN/CHAT_ID not set.")
        return False
    try:
        if len(text) > 3500:
            text = text[:3500] + "\n…(truncated)"
        payload = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            json.loads(response.read().decode("utf-8"))
        print("[notify] Telegram message sent.")
        return True
    except Exception as error:
        print(f"[notify] Telegram failed (continuing): {error}")
        return False


def send_email(
    host: str,
    port: int,
    user: str,
    password: str,
    to_addr: str,
    from_addr: str,
    subject: str,
    body: str,
) -> bool:
    if not host or not to_addr:
        print("[notify] Email skipped: SMTP_HOST/SMTP_TO not set.")
        return False
    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = from_addr or user
        message["To"] = to_addr
        message.set_content(body)
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
        print(f"[notify] Email sent to {to_addr}.")
        return True
    except Exception as error:
        print(f"[notify] Email failed (continuing): {error}")
        return False
