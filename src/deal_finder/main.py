#!/usr/bin/env python
"""Command-line entry points.

    deal_finder serve                      # open the app at http://127.0.0.1:8000
    deal_finder hunt "smart casual" 400    # run one hunt in the terminal
    crewai run                             # same as `hunt` with the defaults below
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from deal_finder.models import CATEGORIES, REGIONS, FabricPolicy, HuntRequest

load_dotenv()

DEFAULT = HuntRequest(brief="Smart casual, 30 year old male", budget=400, region="au")


def _hunt(request: HuntRequest) -> None:
    from deal_finder.service import Hunt, HuntStore, execute, missing_keys

    if missing := missing_keys():
        sys.exit(f"Missing environment variables: {', '.join(missing)}. See README.md.")
    hunt = execute(Hunt(request=request), HuntStore())
    if hunt.status == "failed":
        sys.exit(f"Hunt failed: {hunt.error}")
    r = hunt.report
    sym = request.region_info.symbol
    print(f"\n{len(r.picks)} picks for {sym}{r.total:,.2f} (saved {sym}{r.total_savings:,.2f}).")
    for d in r.picks:
        off = f"  -{d.discount_pct}%" if d.discount_pct else ""
        print(f"  {d.garment:<22} {sym}{d.price:>8,.2f}{off:<6} {d.retailer}: {d.url}")
    print("\nFull guide written to output/deals.md")


def run() -> None:
    """`crewai run` and `deal_finder hunt` land here."""
    p = argparse.ArgumentParser(prog="deal_finder hunt", description="Find menswear deals.")
    p.add_argument("brief", nargs="?", default=DEFAULT.brief)
    p.add_argument("budget", nargs="?", type=float, default=DEFAULT.budget)
    p.add_argument("--region", default="au", choices=sorted(REGIONS))
    p.add_argument("--sizes", default="")
    p.add_argument("--categories", nargs="*", default=[], metavar="CAT",
                   help=f"Any of: {', '.join(CATEGORIES)}")
    p.add_argument("--fabric", default="natural", choices=[f.value for f in FabricPolicy])
    p.add_argument("--min-discount", type=int, default=0)
    p.add_argument("--notes", default="")
    a = p.parse_args(sys.argv[2:] if sys.argv[1:2] == ["hunt"] else sys.argv[1:])
    _hunt(HuntRequest(
        brief=a.brief, budget=a.budget, region=a.region, sizes=a.sizes,
        categories=a.categories, fabric=a.fabric, min_discount=a.min_discount, notes=a.notes,
    ))


def serve() -> None:
    p = argparse.ArgumentParser(prog="deal_finder serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(sys.argv[2:] if sys.argv[1:2] == ["serve"] else sys.argv[1:])

    import uvicorn

    from deal_finder.web.app import create_app

    print(f"Deal Finder is running at http://{a.host}:{a.port}")
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")


def cli() -> None:
    """`deal_finder <command>`."""
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        serve()
    elif command == "hunt":
        run()
    else:
        sys.exit("Usage: deal_finder [serve|hunt] ...  (try `deal_finder hunt --help`)")


def _crew():
    from deal_finder.crew import DealFinderCrew

    return DealFinderCrew(DEFAULT).crew()


def train() -> None:
    _crew().train(n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=DEFAULT.crew_inputs())


def replay() -> None:
    _crew().replay(task_id=sys.argv[1])


def test() -> None:
    _crew().test(n_iterations=int(sys.argv[1]), eval_llm=sys.argv[2], inputs=DEFAULT.crew_inputs())


def run_with_trigger() -> None:
    """Run from a CrewAI AMP trigger whose JSON payload holds HuntRequest fields."""
    if len(sys.argv) < 2:
        sys.exit("Pass the trigger payload as a JSON argument.")
    payload = json.loads(sys.argv[1])
    _hunt(HuntRequest.model_validate({**DEFAULT.model_dump(), **payload}))


if __name__ == "__main__":
    cli()
