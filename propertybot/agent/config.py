"""Agent harness configuration: minimal .env loading plus settings."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path: Path | str = ".env") -> None:
    """Load KEY=VALUE lines into os.environ without third-party deps."""
    file_path = Path(path)
    if not file_path.exists():
        return
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class AgentConfig:
    openrouter_api_key: str = ""
    openrouter_model: str = "meta/muse-spark-1.3"
    top_n: int = 5
    db_path: Path = field(default_factory=lambda: Path("data/propertybot.db"))
    mall_db_path: Path = field(
        default_factory=lambda: Path("data/mall_directory/mall_directory.db")
    )
    reports_dir: Path = field(default_factory=lambda: Path("data/reports"))
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_to: str = ""
    smtp_from: str = ""

    @classmethod
    def from_env(cls) -> "AgentConfig":
        load_dotenv()
        db_path = Path(os.environ.get("PROPERTYBOT_DB", "data/propertybot.db"))
        return cls(
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            openrouter_model=os.environ.get(
                "OPENROUTER_MODEL", "meta/muse-spark-1.3"
            ),
            top_n=_get_int("AGENT_TOP_N", 5),
            db_path=db_path,
            mall_db_path=Path(
                os.environ.get(
                    "MALL_DB_PATH", "data/mall_directory/mall_directory.db"
                )
            ),
            reports_dir=Path(os.environ.get("AGENT_REPORTS_DIR", "data/reports")),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=_get_int("SMTP_PORT", 587),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_pass=os.environ.get("SMTP_PASS", ""),
            smtp_to=os.environ.get("SMTP_TO", ""),
            smtp_from=os.environ.get(
                "SMTP_FROM", os.environ.get("SMTP_USER", "")
            ),
        )

    def require_llm(self) -> None:
        if not self.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — add it to .env "
                "(see .env.example) or run with --dry-run."
            )
