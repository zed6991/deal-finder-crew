"""A small, thread-safe log of what a hunt is doing.

Tools write to it (searches run, pages checked, links seen) and the web app
reads it to show live progress.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

STAGES = [
    ("plan", "Planning the outfit"),
    ("hunt", "Searching shops"),
    ("verify", "Checking prices"),
    ("assess", "Judging style and value"),
]


class Tracker:
    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._on_change = on_change
        self.seen_urls: set[str] = set()
        self.events: list[dict] = []
        self.stage = 0  # index into STAGES of the task now running
        self.searches = 0
        self.pages = 0

    def _changed(self) -> None:
        if self._on_change:
            self._on_change()

    def log(self, kind: str, text: str) -> None:
        with self._lock:
            self.events.append({"t": time.time(), "kind": kind, "text": text})
            del self.events[:-200]
        self._changed()

    def saw(self, *urls: str | None) -> None:
        with self._lock:
            self.seen_urls.update(u for u in urls if u)

    def search_done(self, query: str, hits: int) -> None:
        with self._lock:
            self.searches += 1
        self.log("search", f"“{query}” · {hits} results")

    def page_done(self, url: str, ok: bool) -> None:
        with self._lock:
            self.pages += 1
        self.log("page", ("Checked " if ok else "Couldn’t read ") + url)

    def next_stage(self) -> None:
        with self._lock:
            self.stage = min(self.stage + 1, len(STAGES))
        self._changed()

    def task_done(self, _output: object = None) -> None:
        """Crew task_callback: the running task finished."""
        self.next_stage()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "stage": self.stage,
                "stages": [label for _, label in STAGES],
                "searches": self.searches,
                "pages": self.pages,
                "events": list(self.events[-40:]),
            }
