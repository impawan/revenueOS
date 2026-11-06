from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .agent.graph import AgentDeps, AgentRunner
from .config import ARCHITECT, Settings, from_env
from .events import CLOCK_ADVANCED, EventStore
from .ingest import MalformedPayload, ingest_won
from .library import load_templates
from .models import PocPlan
from .retrieval import Retriever
from .seed import inject, seed
from .state import build_state, derive_briefs
from .watcher import tick, watch_forever

STATIC = Path(__file__).parent / "static"


class Decision(BaseModel):
    action: Literal["accept", "edit", "reject"]
    plan: PocPlan | None = None
    reason: str = ""
    draft_event_id: int


class Advance(BaseModel):
    hours: float = Field(default=24.0, ge=0)


class Ctx:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.reset_on_start and settings.db_path != ":memory:" and os.path.exists(settings.db_path):
            os.remove(settings.db_path)
        self.store = EventStore(settings.db_path)
        self.templates = load_templates()
        self.retriever = Retriever(self.templates)
        self.runner = self._new_runner()

    def _new_runner(self) -> AgentRunner:
        client = None
        if self.settings.agent_mode == "auto" and self.settings.anthropic_api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return AgentRunner(AgentDeps(store=self.store, retriever=self.retriever,
                                     llm_client=client, model=self.settings.model))

    def state(self) -> dict:
        return build_state(self.store, self.retriever.templates)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()
    ctx = Ctx(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if settings.seed_on_start and not ctx.store.all():
            await asyncio.to_thread(seed, ctx.store, ctx.runner, settings.slack_url)
        if settings.watcher_interval > 0:
            task = asyncio.create_task(watch_forever(ctx.store, settings.watcher_interval, settings.slack_url))
        yield
        if task:
            task.cancel()

    app = FastAPI(title="RevenueOS", lifespan=lifespan)

    @app.get("/")
    def index():
        page = STATIC / "index.html"
        if page.exists():
            return FileResponse(page)
        return HTMLResponse("<title>RevenueOS</title><h1>RevenueOS</h1>")

    @app.post("/webhooks/crm")
    def crm_webhook(payload: dict, bg: BackgroundTasks):
        try:
            brief_id = ingest_won(ctx.store, payload)
        except MalformedPayload as e:
            raise HTTPException(422, str(e))
        except ValueError as e:
            return {"ignored": True, "reason": str(e)}
        if brief_id is None:
            return {"ignored": True, "reason": "duplicate"}
        bg.add_task(ctx.runner.start, brief_id)
        return {"accepted": True, "brief_id": brief_id}

    @app.get("/api/state")
    def get_state():
        return ctx.state()

    @app.post("/api/briefs/{brief_id}/decision")
    def decide(brief_id: str, d: Decision):
        if d.action == "edit" and d.plan is None:
            raise HTTPException(422, "edit requires a plan")

        def guard():
            brief = next((b for b in derive_briefs(ctx.store.all(), ctx.store.now())
                          if b.brief_id == brief_id), None)
            if brief is None:
                raise HTTPException(404, "unknown brief")
            if brief.status != "drafted":
                raise HTTPException(409, f"brief is {brief.status}; nothing to decide")
            if brief.draft_event_id != d.draft_event_id:
                raise HTTPException(409, f"stale draft: current draft is #{brief.draft_event_id}")

        ctx.runner.decide(brief_id, {"action": d.action, "owner": ARCHITECT, "reason": d.reason,
                                     "plan": d.plan.model_dump() if d.plan else None}, guard=guard)
        return ctx.state()

    @app.post("/api/clock/advance")
    def advance(a: Advance):
        ctx.store.append(CLOCK_ADVANCED, "demo-control", "-", "-", {"hours": a.hours})
        tick(ctx.store, settings.slack_url)
        return ctx.state()

    @app.post("/api/demo/inject")
    def demo_inject():
        return {"brief_id": inject(ctx.store, ctx.runner)}

    @app.post("/api/demo/reset")
    def demo_reset():
        ctx.store.reset()
        ctx.runner = ctx._new_runner()
        if settings.seed_on_start:
            seed(ctx.store, ctx.runner, settings.slack_url)
        return ctx.state()

    return app
