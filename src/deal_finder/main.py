#!/usr/bin/env python
"""Command line.

    deal_finder                     open the app (same as `serve`)
    deal_finder sync                refresh every shop's catalogue (free)
    deal_finder deals [words]       top deals in the terminal
    deal_finder outfit "brief" 500  build an outfit within a budget
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from deal_finder.normalize import CATEGORIES

load_dotenv()


def _db():
    from deal_finder.db import DB

    return DB()


def serve(args) -> None:
    import uvicorn

    from deal_finder.web.app import create_app

    print(f"Deal Finder is running at http://{args.host}:{args.port}")
    print("The first run downloads every shop's catalogue, which takes a couple of minutes.")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


def sync(args) -> None:
    from deal_finder.stores import BY_KEY, FEEDS
    from deal_finder.sync import Syncer

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stores = [BY_KEY[k] for k in args.stores] if args.stores else FEEDS
    results = Syncer(_db()).sync(stores)
    failed = [k for k, r in results.items() if "error" in r]
    print(f"Synced {len(results) - len(failed)} of {len(results)} shops.")
    if failed:
        sys.exit(f"Failed: {', '.join(failed)}")


def _line(d: dict) -> str:
    was = f" (was ${d['was_price']:,.0f}, -{d['discount_pct']}%)" if d["was_price"] else ""
    return f"{d['score']:>3}  ${d['price']:>7,.2f}{was:<22} {d['store_name']:<14} {d['title'][:48]}"


def deals(args) -> None:
    from deal_finder.deals import Filters, search

    db = _db()
    f = Filters(
        q=" ".join(args.words), categories=args.category or [], min_discount=args.min_discount,
        max_price=args.max_price, premium_only=args.premium, limit=args.limit,
    )
    r = search(db, f)
    if not r["total"]:
        print("No deals found. Run `deal_finder sync` first if you have not yet.")
        return
    print(f"{r['total']} matching deals. Top {len(r['items'])}:\n")
    for d in r["items"]:
        print(_line(d))
        print(f"      {d['url']}")


def outfit(args) -> None:
    from deal_finder import stylist
    from deal_finder.deals import Filters

    db = _db()
    plan = stylist.plan_for(args.brief, db, use_ai=args.ai)
    sizes = db.get("settings", {}).get("sizes", {})
    result = stylist.build_outfit(db, plan, args.budget, sizes, Filters(my_sizes=bool(sizes)))
    print(f"{plan.look} (read by {plan.source}), budget ${args.budget:,.0f}\n")
    for s in result["slots"]:
        d = s["pick"]
        print(f"{s['slot']['category']:<12}", _line(d) if d else "nothing within budget")
        if d:
            print(f"{'':<12}      {d['url']}")
    print(f"\nTotal ${result['total']:,.2f} · saved ${result['saved']:,.2f} · ${result['remaining']:,.2f} left")


def cli() -> None:
    p = argparse.ArgumentParser(prog="deal_finder", description="Find menswear deals in Australian shops.")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("serve", help="open the app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(run=serve)

    s = sub.add_parser("sync", help="refresh shop catalogues")
    s.add_argument("stores", nargs="*", help="shop keys, e.g. mjbale peterjackson (default: all)")
    s.set_defaults(run=sync)

    s = sub.add_parser("deals", help="list top deals")
    s.add_argument("words", nargs="*")
    s.add_argument("--category", nargs="*", choices=CATEGORIES)
    s.add_argument("--min-discount", type=int, default=0)
    s.add_argument("--max-price", type=float)
    s.add_argument("--premium", action="store_true", help="premium shops only")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(run=deals)

    s = sub.add_parser("outfit", help="build an outfit within a budget")
    s.add_argument("brief")
    s.add_argument("budget", type=float)
    s.add_argument("--ai", action="store_true", help="read the brief with Claude (needs ANTHROPIC_API_KEY)")
    s.set_defaults(run=outfit)

    args = p.parse_args()
    if not args.command:
        args = p.parse_args(["serve"])
    args.run(args)


if __name__ == "__main__":
    cli()
