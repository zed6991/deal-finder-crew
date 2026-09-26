# Notes for coding assistants

Deal Finder is a plain Python app (FastAPI + SQLite + vanilla JS). It no
longer uses CrewAI.

* Run the tests with `uv run pytest`. They must not touch the network; use
  the fakes in `tests/` (fake `fetch`, fake Serper `post`, fake Anthropic client).
* Keep costs at zero by default. Anything paid (Claude, Serper) must be
  optional, capped or cached, and shown to the user as paid.
* Category, gender and skip rules live in `normalize.py`. Rule order matters;
  add a test in `tests/test_normalize.py` for any new case.
* Scoring lives in `deals.py`; keep the README's scoring table in step with it.
* The front end follows Apple's Human Interface Guidelines. Reuse the tokens
  and components in `web/static/app.css` (grouped lists, segmented controls,
  switches, sheets) rather than adding new styles.
* Claude calls go through the official `anthropic` SDK (`stylist.py`).
