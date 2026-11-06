from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ARCHITECT = "Marcus Reyes"
ESCALATION_OWNER = "Dana Whitfield"
ESCALATION_ROLE = "US PreSales Director"
ESCALATION_CHANNEL = "#us-presales-escalations"


@dataclass
class Settings:
    db_path: str = "data/revenueos.db"
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-5-5"
    agent_mode: str = "auto"  # auto | fallback
    slack_url: str = ""
    watcher_interval: float = 5.0  # seconds; 0 disables the background watcher
    seed_on_start: bool = True
    reset_on_start: bool = True


def load_dotenv_file(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def from_env() -> Settings:
    load_dotenv_file()
    g = os.environ.get
    return Settings(
        db_path=g("REVENUEOS_DB", "data/revenueos.db"),
        anthropic_api_key=g("ANTHROPIC_API_KEY", ""),
        model=g("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        agent_mode=g("AGENT_MODE", "auto"),
        slack_url=g("SLACK_WEBHOOK_URL", ""),
        watcher_interval=float(g("WATCHER_INTERVAL", "5")),
    )
