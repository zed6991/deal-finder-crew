"""Run a hunt end to end and keep its record on disk."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from deal_finder.models import AssessmentList, HuntRequest, LeadList, Report
from deal_finder.progress import Tracker
from deal_finder.scoring import finalize, to_markdown

OUTPUT_DIR = Path(os.environ.get("DEAL_FINDER_OUTPUT", "output"))
REQUIRED_KEYS = ("SERPER_API_KEY",)


def missing_keys() -> list[str]:
    """Environment variables a live hunt needs but does not have."""
    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k)]
    llm_keys = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY")
    if not any(os.environ.get(k) for k in llm_keys) and not os.environ.get("MODEL", "").startswith("ollama"):
        missing.append("OPENAI_API_KEY (or another LLM key)")
    return missing


class Hunt(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    request: HuntRequest
    status: Literal["queued", "running", "done", "failed"] = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    error: str | None = None
    progress: dict = Field(default_factory=dict)
    report: Report | None = None
    sample: bool = False

    def summary(self) -> dict:
        r = self.report
        return {
            "id": self.id,
            "brief": self.request.brief,
            "status": self.status,
            "created_at": self.created_at,
            "currency": self.request.region_info.currency,
            "budget": self.request.budget,
            "picks": len(r.picks) if r else 0,
            "found": len(r.deals) if r else 0,
            "total": r.total if r else 0,
            "savings": r.total_savings if r else 0,
            "sample": self.sample,
        }


class HuntStore:
    """One JSON file per hunt under ``output/hunts``."""

    def __init__(self, root: Path = OUTPUT_DIR) -> None:
        self.dir = root / "hunts"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, hunt_id: str) -> Path:
        if not hunt_id.isalnum():
            raise KeyError(hunt_id)
        return self.dir / f"{hunt_id}.json"

    def save(self, hunt: Hunt) -> None:
        path = self._path(hunt.id)
        tmp = path.with_suffix(".tmp")
        with self._lock:
            tmp.write_text(hunt.model_dump_json(indent=2))
            tmp.replace(path)

    def get(self, hunt_id: str) -> Hunt:
        path = self._path(hunt_id)
        if not path.exists():
            raise KeyError(hunt_id)
        return Hunt.model_validate_json(path.read_text())

    def all(self) -> list[Hunt]:
        hunts = []
        for p in self.dir.glob("*.json"):
            try:
                hunts.append(Hunt.model_validate_json(p.read_text()))
            except ValueError:
                continue
        return sorted(hunts, key=lambda h: h.created_at, reverse=True)

    def delete(self, hunt_id: str) -> None:
        self._path(hunt_id).unlink(missing_ok=True)


def run_crew(request: HuntRequest, tracker: Tracker) -> Report:
    """Kick off the crew and turn its typed outputs into a scored report."""
    from deal_finder.crew import DealFinderCrew  # heavy import; only when needed

    result = DealFinderCrew(request, tracker).crew().kickoff(inputs=request.crew_inputs())
    outputs = {t.name: t.pydantic for t in result.tasks_output}

    checked = outputs.get("verify_task")
    hunted = outputs.get("hunt_task")
    leads = (checked if isinstance(checked, LeadList) and checked.leads else hunted)
    assessed = outputs.get("assess_task")
    return finalize(
        request,
        leads.leads if isinstance(leads, LeadList) else [],
        assessed.assessments if isinstance(assessed, AssessmentList) else [],
        seen_urls=tracker.seen_urls,
    )


def execute(hunt: Hunt, store: HuntStore, runner=run_crew) -> Hunt:
    """Run a stored hunt, saving progress as it goes."""

    def sync() -> None:
        hunt.progress = tracker.snapshot()
        store.save(hunt)

    tracker = Tracker(on_change=sync)
    hunt.status = "running"
    sync()
    try:
        hunt.report = runner(hunt.request, tracker)
        hunt.status = "done"
        write_markdown(hunt.report, hunt.request.region_info.symbol)
    except Exception as exc:  # report any failure to the user
        hunt.status = "failed"
        hunt.error = f"{type(exc).__name__}: {exc}"
    hunt.finished_at = datetime.now(timezone.utc)
    sync()
    return hunt


def write_markdown(report: Report, symbol: str, path: Path | None = None) -> Path:
    path = path or OUTPUT_DIR / "deals.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(report, symbol))
    return path


def load_sample() -> Hunt:
    """A finished hunt, from a real earlier run, for trying the app without keys."""
    data = json.loads((Path(__file__).parent / "sample_hunt.json").read_text())
    return Hunt.model_validate(data)
