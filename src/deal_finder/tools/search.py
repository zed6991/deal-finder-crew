"""Serper-backed search tools that remember every link they return."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from crewai.tools import BaseTool, EnvVar
from pydantic import BaseModel, Field

from deal_finder.progress import Tracker

SERPER_URL = "https://google.serper.dev"


class SearchInput(BaseModel):
    query: str = Field(..., description="What to search for, e.g. 'mens merino crew neck navy sale'")


class _SerperTool(BaseTool):
    args_schema: type[BaseModel] = SearchInput
    country: str = "au"
    n_results: int = 10
    tracker: Any = Field(default=None, exclude=True)
    env_vars: list[EnvVar] = Field(
        default_factory=lambda: [
            EnvVar(name="SERPER_API_KEY", description="API key for Serper", required=True)
        ]
    )
    endpoint: str = "search"

    def _post(self, query: str) -> dict:
        resp = requests.post(
            f"{SERPER_URL}/{self.endpoint}",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
            json={"q": query, "gl": self.country, "num": self.n_results},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def _format(self, data: dict) -> list[dict]:
        raise NotImplementedError

    def _run(self, query: str) -> str:
        try:
            rows = self._format(self._post(query))[: self.n_results]
        except Exception as exc:  # the agent should see the error, not crash
            return f"Search failed: {exc}"
        if isinstance(self.tracker, Tracker):
            self.tracker.saw(*(r.get("link") for r in rows))
            self.tracker.search_done(query, len(rows))
        if not rows:
            return "No results."
        return json.dumps(rows, ensure_ascii=False)


class ShoppingSearchTool(_SerperTool):
    name: str = "shopping_search"
    description: str = (
        "Search Google Shopping for products. Returns title, retailer (source), "
        "price, link and image for each product. Best first tool for prices."
    )
    endpoint: str = "shopping"

    def _format(self, data: dict) -> list[dict]:
        return [
            {
                "title": r.get("title"),
                "source": r.get("source"),
                "price": r.get("price"),
                "link": r.get("link"),
                "imageUrl": r.get("imageUrl"),
                "rating": r.get("rating"),
            }
            for r in data.get("shopping", [])
            if r.get("link")
        ]


class WebSearchTool(_SerperTool):
    name: str = "web_search"
    description: str = (
        "Search the web. Use it to find a retailer's own product page, or sale "
        "pages, when shopping_search gives no direct retailer link."
    )
    endpoint: str = "search"

    def _format(self, data: dict) -> list[dict]:
        return [
            {"title": r.get("title"), "link": r.get("link"), "snippet": r.get("snippet")}
            for r in data.get("organic", [])
            if r.get("link")
        ]
