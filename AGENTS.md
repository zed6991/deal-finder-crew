# Notes for coding assistants

Deal Finder is a plain Python app (FastAPI + SQLite + vanilla JS). It no
longer uses CrewAI.

* Run the tests with `uv run pytest`. They must not touch the network; use
  the fakes in `tests/` (fake `fetch`, fake Serper `post`, fake Anthropic client).
  Set `TEST_DATABASE_URL` to a scratch Postgres to run the database tests there too.
* SQL must work on both SQLite and Postgres: `?` placeholders, `ON CONFLICT`
  upserts, no `PRAGMA` outside `db.py`, no SQLite-only functions.
* The app also runs on Vercel (`app.py`, `vercel.json`): no background threads
  there, each request under 300 s, and every page behind `APP_PASSWORD`.
* Keep costs at zero by default. Anything paid (Claude, Serper) must be
  optional, capped or cached, and shown to the user as paid.
* Category, gender and skip rules live in `normalize.py`. Rule order matters;
  add a test in `tests/test_normalize.py` for any new case.
* THE ICONIC and David Jones are read from their men's sale listing pages
  (`scrape.py`, store kind `listing`). The parsers depend on each site's markup;
  when one breaks, refresh the markup copies in `tests/test_scrape.py` and fix
  the parser. Keep page caps low enough for one shop to sync well under 300 s.
* Scoring lives in `deals.py`; keep the README's scoring table in step with it.
* The front end follows Apple's Human Interface Guidelines in the Liquid Glass
  style (iOS 26): navigation and controls float on the glass material
  (`--glass*` tokens, one shared rule in `app.css`); content stays solid.
  Reuse the tokens and components in `web/static/app.css` (grouped lists,
  segmented controls, switches, sheets) rather than adding new styles.
* Claude calls go through the official `anthropic` SDK (`stylist.py`).
