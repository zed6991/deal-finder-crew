# Deal Finder

Find genuine markdowns on mid-to-premium menswear from Australian shops.

Deal Finder reads the full public catalogues of 13 shops for free, keeps every
price it sees, and ranks what is actually a good buy. There are no AI agents
and no search costs unless you turn them on.

![Deals on desktop](docs/deals.jpg)

<p>
<img src="docs/phone-deals.jpg" width="260" alt="Deals on a phone">
<img src="docs/phone-product.jpg" width="260" alt="A product with its price history">
</p>

## Shops

| Free: full catalogue, every price change | Tier |
|---|---|
| M.J. Bale, P. Johnson, Harrolds, Aquila, Venroy, Bassike, Calibre | Premium |
| Peter Jackson, Industrie, Academy Brand, Jac+Jack, Oxford, Gazman | Mid |

These shops run on Shopify, which publishes each catalogue with sale and full
prices. Deal Finder keeps only menswear, and skips gift cards, socks,
underwear, eyewear and shoe care.

THE ICONIC, David Jones and Country Road block catalogue reads. You can search
them on request through Serper (optional, about A$0.002 a search, capped per
day, cached for a day). Only the current price is known for those, so they
appear beside the scored deals rather than among them.

To add a Shopify shop, add one line to `src/deal_finder/stores.py`.

## What counts as a good deal

Each item gets a score out of 100:

| Part | Points |
|---|---|
| Markdown: the shop's "was" discount, or a drop we saw ourselves in the last 14 days; full marks at 60% off | 45 |
| Dollars saved; full marks at $200 | 10 |
| Lowest price we have recorded (after 3 days of tracking) | 15 |
| Fabric: natural 15, natural with a little stretch 9, unknown 5, synthetic 0 | 15 |
| Shop tier: premium 15, mid 8 | 15 |

Gazman and Oxford mark most of their range down all the time, so their "was"
discounts count at half and carry a **Store-wide sale** badge. You can hide
them in Filters. **Just dropped** means the price fell since an earlier check.

## Run it

You need Python 3.10–3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run deal_finder
```

Open <http://127.0.0.1:8000>. The first start downloads every catalogue,
which takes about two minutes. After that, shops refresh every 12 hours while
the app runs, and **Stores → Update Now** refreshes them on demand.

Keys are optional. Copy `.env.example` to `.env` to add them:

* `ANTHROPIC_API_KEY` lets the Outfit page read free-text briefs with Claude.
  That is one small call per new brief, and repeating a brief is free. Without
  a key, looks like "office" or "summer wedding" use built-in presets, and
  lists like "navy blazer, white shirt, brown loafers" are read word by word.
* `SERPER_API_KEY` turns on searching THE ICONIC, David Jones and Country Road.

## The app

* **Deals**: search, category chips, and filters for discount, price, your
  sizes, fabric, premium shops, or particular shops. Sort by best deal,
  discount, price or newest. Tap an item to see its price history, sizes in
  stock (yours highlighted) and why it scored as it did.
* **Outfit**: describe a look and a budget. You get the best-value piece for
  each part, within budget and in your sizes, with alternatives to tap and swap.
* **Saved**: tap the heart to watch a price. Saved items show how much they
  have moved since you saved them.
* **Stores**: your sizes, each shop's status, and paid-search usage.

The design follows Apple's Human Interface Guidelines: system font, iOS
colours with automatic dark mode, grouped lists, segmented controls, switches
and sheets. It uses a sidebar on wide screens and a tab bar on phones.

![Outfit builder](docs/outfit.jpg)

## Command line

```bash
uv run deal_finder sync                          # refresh every shop (free)
uv run deal_finder sync mjbale peterjackson      # just these
uv run deal_finder deals linen --category Shirts --min-discount 40
uv run deal_finder deals --premium --max-price 300
uv run deal_finder outfit "smart casual dinner, navy and stone" 600
uv run deal_finder outfit "something for a garden party" 500 --ai
```

## How it fits together

```
src/deal_finder/
├── stores.py      the shops, their tier, and how to tell menswear apart
├── sync.py        downloads catalogues politely (paged, retried, throttled)
├── normalize.py   raw product → category, fabric, colour, sizes, price, was price
├── db.py          SQLite: products, price history, saved items, settings, cache
├── deals.py       deal score, search and filters, shop mixing
├── stylist.py     brief → shopping list (presets, keywords, or Claude) → outfit
├── extra.py       optional paid search for shops that block catalogue reads
├── main.py        command line
└── web/           FastAPI app and the front end (plain HTML, CSS and JS)
```

Data lives in `data/deals.db`. Delete it to start afresh.

## Tests

```bash
uv run pytest
```

62 tests cover categories, fabric rules, menswear filtering, price tracking,
scoring, sizes, search, outfits, the Claude call (with a fake client), paid
search limits and the API. None of them use the network.
