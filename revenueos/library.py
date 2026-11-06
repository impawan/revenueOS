from __future__ import annotations

import json
from pathlib import Path

from .models import Template

DEFAULT_PATH = Path(__file__).parent / "data" / "templates.json"


def load_templates(path: str | Path | None = None) -> list[Template]:
    raw = json.loads(Path(path or DEFAULT_PATH).read_text())
    return [Template(**t) for t in raw]
