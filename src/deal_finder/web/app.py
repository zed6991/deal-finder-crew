"""JSON API and the static front end."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from deal_finder import analytics, auth, extra, stylist
from deal_finder.db import DB, ON_VERCEL
from deal_finder.deals import SIZE_GROUPS, Filters, sale_shares, score_row, search
from deal_finder.normalize import CATEGORIES
from deal_finder.stores import BY_KEY, SEARCHED, STORES
from deal_finder.sync import Syncer

STATIC = Path(__file__).parent / "static"
DEFAULT_SETTINGS = {"sizes": {g: "" for g in SIZE_GROUPS}}
# Leave headroom under Vercel's 300 s function limit.
CRON_BUDGET = float(os.environ.get("DEAL_FINDER_CRON_BUDGET", "240"))
OPEN_PATHS = ("/login", "/api/login", "/static/", "/api/cron/", "/favicon.ico")


class Settings(BaseModel):
    sizes: dict[str, str] = Field(default_factory=dict)


class OutfitRequest(BaseModel):
    brief: str = Field(min_length=2, max_length=400)
    budget: float = Field(gt=0, le=20_000)
    use_ai: bool = False
    my_sizes: bool = True
    premium_only: bool = False
    fabric: Literal["any", "natural", "stretch"] = "any"
    include_storewide: bool = True
    stores: list[str] = Field(default_factory=list)


class Login(BaseModel):
    password: str = Field(max_length=200)


def create_app(db: DB | None = None, syncer: Syncer | None = None, auto_sync: bool | None = None) -> FastAPI:
    db = db or DB()
    syncer = syncer or Syncer(db)
    app = FastAPI(title="Deal Finder", docs_url=None if ON_VERCEL else "/api/docs", redoc_url=None)
    # Serverless functions freeze between requests, so no background loop there;
    # the daily cron and the page itself keep shops fresh instead.
    if auto_sync if auto_sync is not None else not ON_VERCEL:
        syncer.keep_fresh()

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if path.startswith(OPEN_PATHS):
            return await call_next(request)
        if not auth.configured():
            if auth.required():
                return HTMLResponse(auth.SETUP_PAGE, status_code=503)
            return await call_next(request)
        if auth.valid(request.cookies.get(auth.COOKIE)):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in first"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    def settings() -> dict:
        s = db.get("settings", DEFAULT_SETTINGS)
        return {"sizes": {**DEFAULT_SETTINGS["sizes"], **s.get("sizes", {})}}

    def item(pid: str) -> dict:
        row = db.product(pid)
        if row is None:
            raise HTTPException(404, "No such product")
        return score_row(row, sale_shares(db))

    # ── Sign in ───────────────────────────────────────────────

    @app.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(STATIC / "login.html")

    @app.post("/api/login", status_code=204)
    def login(body: Login, response: Response) -> None:
        if not auth.configured():
            return
        if not auth.check_password(body.password):
            time.sleep(1)  # slow down guessing
            raise HTTPException(401, "Wrong password")
        auth.set_cookie(response)

    @app.post("/api/logout", status_code=204)
    def logout(response: Response) -> None:
        response.delete_cookie(auth.COOKIE, path="/")

    # ── Status and syncing ────────────────────────────────────

    @app.get("/api/status")
    def status() -> dict:
        syncs = db.syncs()
        counts = {r["store"]: (r["n"], r["deals"]) for r in db.query(
            "SELECT store, COUNT(*) n, COUNT(was_price) deals FROM products "
            "WHERE active = 1 AND in_stock = 1 GROUP BY store")}
        shares = sale_shares(db)
        stale = {s.key for s in syncer.stale()}
        stores = []
        for s in STORES:
            sync = syncs.get(s.key)
            n, deals = counts.get(s.key, (0, 0))
            stores.append({
                "key": s.key, "name": s.name, "tier": s.tier, "kind": s.kind, "domain": s.domain,
                "products": n, "on_sale": deals or 0,
                "storewide": shares.get(s.key, 0) >= 0.6,
                "synced_at": sync["at"] if sync else None,
                "ok": bool(sync["ok"]) if sync else None,
                "error": sync["error"] if sync else None,
                "syncing": s.key in syncer.running,
                "stale": s.key in stale,
            })
        return {
            "stores": stores,
            "categories": CATEGORIES,
            "size_groups": {g: cats for g, cats in SIZE_GROUPS.items()},
            "settings": settings(),
            "ai": stylist.ai_available(),
            "ai_model": stylist.ai_model(),
            "extra": extra.available(),
            "extra_used_today": extra.used_today(db),
            "extra_cap": extra.DAILY_CAP,
            "syncing": bool(syncer.running),
            "empty": not counts,
            "hosted": ON_VERCEL,
            "persistent": db.persistent,
            "signed_in": auth.configured(),
        }

    @app.post("/api/sync/{store}")
    def sync_store(store: str) -> dict:
        """Refresh one shop and wait for it. The page calls this shop by shop."""
        s = BY_KEY.get(store)
        if s is None or s.kind == "search":
            raise HTTPException(404, "No such shop")
        return syncer.sync_store(s)

    @app.get("/api/cron/sync")
    def cron_sync(request: Request) -> dict:
        """Vercel Cron: refresh stale shops until the time budget runs out."""
        if not auth.cron_ok(request.headers.get("authorization")):
            raise HTTPException(401, "Unauthorized")
        return syncer.sync_until(time.monotonic() + CRON_BUDGET)

    # ── Deals ─────────────────────────────────────────────────

    @app.get("/api/deals")
    def deals(
        q: str = "",
        category: list[str] = Query(default=[]),
        store: list[str] = Query(default=[]),
        min_discount: int = Query(0, ge=0, le=95),
        min_price: float | None = Query(None, ge=0),
        max_price: float | None = Query(None, ge=0),
        fabric: Literal["any", "natural", "stretch"] = "any",
        my_sizes: bool = False,
        premium_only: bool = False,
        include_storewide: bool = True,
        sort: Literal["score", "discount", "price", "price_desc", "newest"] = "score",
        limit: int = Query(48, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict:
        f = Filters(
            q=q[:100], categories=[c for c in category if c in CATEGORIES], stores=store,
            min_discount=min_discount, min_price=min_price, max_price=max_price, fabric=fabric, my_sizes=my_sizes,
            premium_only=premium_only, include_storewide=include_storewide, sort=sort,
            limit=limit, offset=offset,
        )
        result = search(db, f, settings()["sizes"])
        saved = db.saved()
        for d in result["items"]:
            d["saved"] = d["id"] in saved
        return result

    @app.get("/api/analytics")
    def insights() -> dict:
        result = analytics.summary(db)
        saved = db.saved()
        for d in [*result["recent_drops"], *result["top_deals"]]:
            d["saved"] = d["id"] in saved
        return result

    @app.get("/api/products/{pid:path}")
    def product(pid: str) -> dict:
        d = item(pid)
        saved = db.saved().get(pid)
        d["history"] = db.history(pid)
        d["saved"] = bool(saved)
        d["saved_price"] = saved["saved_price"] if saved else None
        return d

    @app.get("/api/saved")
    def saved_items() -> list[dict]:
        out = []
        shares = sale_shares(db)
        for pid, s in db.saved().items():
            row = db.product(pid)
            if row is None:
                continue
            d = score_row(row, shares)
            d["saved"] = True
            d["saved_price"] = s["saved_price"]
            d["saved_at"] = s["saved_at"]
            d["change"] = round(d["price"] - s["saved_price"], 2)
            d["active"] = bool(row["active"])
            out.append(d)
        return out

    @app.put("/api/saved/{pid:path}", status_code=204)
    def save(pid: str) -> None:
        try:
            db.save(pid)
        except KeyError:
            raise HTTPException(404, "No such product") from None

    @app.delete("/api/saved/{pid:path}", status_code=204)
    def unsave(pid: str) -> None:
        db.unsave(pid)

    @app.get("/api/settings")
    def get_settings() -> dict:
        return settings()

    @app.put("/api/settings")
    def put_settings(body: Settings) -> dict:
        sizes = {g: (body.sizes.get(g) or "").strip()[:60] for g in SIZE_GROUPS}
        db.put("settings", {"sizes": sizes})
        return settings()

    @app.post("/api/outfit")
    def outfit(body: OutfitRequest) -> dict:
        plan = stylist.plan_for(body.brief, db, use_ai=body.use_ai)
        base = Filters(
            stores=body.stores, fabric=body.fabric, my_sizes=body.my_sizes,
            premium_only=body.premium_only, include_storewide=body.include_storewide,
        )
        result = stylist.build_outfit(db, plan, body.budget, settings()["sizes"], base)
        saved = db.saved()
        for slot in result["slots"]:
            for d in [slot["pick"], *slot["alternates"]]:
                if d:
                    d["saved"] = d["id"] in saved
        return result

    @app.get("/api/extra")
    def extra_search(q: str = Query(min_length=2, max_length=120)) -> dict:
        try:
            return extra.search(db, q)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            raise HTTPException(502, f"Search failed: {exc}") from None

    @app.get("/api/searched-stores")
    def searched_stores() -> list[dict]:
        return [{"key": s.key, "name": s.name} for s in SEARCHED]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
