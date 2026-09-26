"""The web app: a JSON API and the static front end."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from deal_finder.models import CATEGORIES, REGIONS, HuntRequest
from deal_finder.scoring import to_markdown
from deal_finder.service import Hunt, HuntStore, execute, load_sample, missing_keys, run_crew

STATIC = Path(__file__).parent / "static"


class Worker:
    """Runs hunts one at a time, in the order they were asked for."""

    def __init__(self, store: HuntStore, runner=run_crew) -> None:
        self.store = store
        self.runner = runner
        self.jobs: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._loop, daemon=True, name="hunt-worker").start()

    def submit(self, hunt: Hunt) -> None:
        self.store.save(hunt)
        self.jobs.put(hunt.id)

    def _loop(self) -> None:
        while True:
            hunt_id = self.jobs.get()
            try:
                execute(self.store.get(hunt_id), self.store, self.runner)
            except KeyError:
                pass  # deleted while queued


def create_app(store: HuntStore | None = None, runner=run_crew, check_keys=missing_keys) -> FastAPI:
    store = store or HuntStore()
    worker = Worker(store, runner)
    app = FastAPI(title="Deal Finder", docs_url="/api/docs")

    # Hunts cut short by a restart can never finish; say so.
    for h in store.all():
        if h.status in ("queued", "running"):
            h.status, h.error = "failed", "Stopped when the server restarted."
            store.save(h)

    @app.get("/api/config")
    def config() -> dict:
        return {
            "regions": [r.model_dump() for r in REGIONS.values()],
            "categories": CATEGORIES,
            "missing_keys": check_keys(),
        }

    @app.get("/api/hunts")
    def list_hunts() -> list[dict]:
        return [h.summary() for h in store.all()]

    @app.post("/api/hunts", status_code=202)
    def start_hunt(request: HuntRequest) -> dict:
        if missing := check_keys():
            raise HTTPException(400, f"Add {', '.join(missing)} to your .env file first.")
        hunt = Hunt(request=request)
        worker.submit(hunt)
        return hunt.summary()

    @app.post("/api/hunts/sample", status_code=201)
    def add_sample() -> dict:
        hunt = load_sample()
        store.save(hunt)
        return hunt.summary()

    @app.get("/api/hunts/{hunt_id}")
    def get_hunt(hunt_id: str) -> Hunt:
        try:
            return store.get(hunt_id)
        except KeyError:
            raise HTTPException(404, "No such hunt") from None

    @app.get("/api/hunts/{hunt_id}/markdown", response_class=PlainTextResponse)
    def hunt_markdown(hunt_id: str) -> str:
        hunt = get_hunt(hunt_id)
        if not hunt.report:
            raise HTTPException(409, "This hunt has no results yet")
        return to_markdown(hunt.report, hunt.request.region_info.symbol)

    @app.delete("/api/hunts/{hunt_id}", status_code=204)
    def delete_hunt(hunt_id: str) -> None:
        try:
            store.delete(hunt_id)
        except KeyError:
            raise HTTPException(404, "No such hunt") from None

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
