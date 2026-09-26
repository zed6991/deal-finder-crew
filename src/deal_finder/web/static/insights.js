// Insights: the market at a glance. One headline number first, then the
// story behind it — how prices moved, how deep the cuts go, and where.

import { api, el, emptyState, money } from "./ui.js";

const shortDay = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-AU", { day: "numeric", month: "short" });

function stat(value, label, sub = "") {
  return el("div", { class: "stat-tile" },
    el("div", { class: "stat-value", text: value }),
    el("div", { class: "stat-label", text: label }),
    sub && el("div", { class: "stat-sub", text: sub }));
}

function panel(title, sub, ...body) {
  return el("section", { class: "panel" },
    el("div", { class: "panel-head" }, el("h2", { text: title }), sub && el("span", { text: sub })), ...body);
}

// Vertical bars on a shared baseline. Each bar carries its own tooltip text;
// `labelEvery` thins the axis labels on long series.
function columns(data, { value, label, tip, ariaLabel, labelEvery = 1, showValues = false }) {
  const max = Math.max(1, ...data.map(value));
  const bars = data.map((d) => el("div", { class: "col", tabindex: 0, "data-tip": tip(d), "aria-label": tip(d) },
    showValues && el("b", { text: value(d).toLocaleString() }),
    el("i", { style: `height:${Math.max(value(d) ? 3 : 0, (value(d) / max) * 100)}%` })));
  const axis = data.map((d, i) => el("span", { text: i % labelEvery === 0 || i === data.length - 1 ? label(d) : "" }));
  return el("div", { class: "chart", role: "group", "aria-label": ariaLabel },
    el("div", { class: "columns" }, bars),
    el("div", { class: "axis" }, axis));
}

// Horizontal bars, one row each: name, bar, value. Rows can link somewhere.
function barList(rows, { name, value, note, href }) {
  const max = Math.max(1, ...rows.map(value));
  return el("div", { class: "bars" }, rows.map((r) => el(href ? "a" : "div", { class: "bar-row", href: href?.(r) },
    el("span", { class: "bar-name", text: name(r) }),
    el("span", { class: "bar-track" }, el("i", { style: `width:${(value(r) / max) * 100}%` })),
    el("span", { class: "bar-value" }, el("b", { text: value(r).toLocaleString() }), note && el("small", { text: note(r) })))));
}

function rail(title, sub, items, dealCard) {
  return el("section", { class: "rail-section" },
    el("div", { class: "panel-head" }, el("h2", { text: title }), el("span", { text: sub })),
    el("div", { class: "rail" }, items.map((d) => dealCard(d))));
}

function render(a, { page, dealCard, refreshControl }) {
  const t = a.totals;
  const drops = a.drops_by_day.reduce((n, d) => n + d.drops, 0);
  const onSale = a.stores.filter((s) => s.on_sale);

  return page("Insights", { aside: refreshControl() },
    el("p", { class: "subtitle", text: `What’s marked down across ${t.shops} shops right now.` }),
    el("section", { class: "headline" },
      el("div", { class: "headline-value", text: money(Math.round(t.savings)) }),
      el("div", { class: "headline-label", text: "in markdowns on the racks today" })),
    el("div", { class: "stat-grid" },
      stat(t.on_sale.toLocaleString(), "on sale", `of ${t.tracked.toLocaleString()} items tracked`),
      stat(t.great.toLocaleString(), "great deals", "scored 75 or more"),
      stat(`${t.avg_discount}%`, "average discount", "across items on sale"),
      stat(`${t.best_discount}%`, "deepest discount", "the single biggest cut")),
    panel("Price drops", `${drops.toLocaleString()} in the last 30 days`,
      drops ? columns(a.drops_by_day, {
        value: (d) => d.drops, label: (d) => shortDay(d.day), labelEvery: 7,
        tip: (d) => `${shortDay(d.day)}: ${d.drops.toLocaleString()} ${d.drops === 1 ? "drop" : "drops"}`,
        ariaLabel: "Price drops per day over the last 30 days",
      }) : el("p", { class: "chart-empty", text: "Drops show up here as shops cut prices. Refresh over the next few days to build the picture." })),
    el("div", { class: "split" },
      panel("How deep the cuts go", "Items on sale, by discount",
        columns(a.discounts, {
          value: (b) => b.count, label: (b) => b.label, showValues: true,
          tip: (b) => `${b.label} off: ${b.count.toLocaleString()} items`,
          ariaLabel: "Items on sale grouped by discount",
        })),
      panel("Where the deals are", "Items on sale, by shop",
        barList(onSale, { name: (s) => s.name, value: (s) => s.on_sale, note: (s) => `${s.avg_discount}% avg`,
          href: (s) => `#/deals?store=${encodeURIComponent(s.key)}` }))),
    panel("By category", "Tap one to browse it",
      barList(a.categories.filter((c) => c.on_sale), {
        name: (c) => c.category, value: (c) => c.on_sale, note: (c) => `${c.avg_discount}% avg`,
        href: (c) => `#/deals?category=${encodeURIComponent(c.category)}`,
      })),
    a.recent_drops.length > 0 && rail("Just dropped", "The biggest price cuts in the last two weeks", a.recent_drops, dealCard),
    a.top_deals.length > 0 && rail("Top deals", "The highest scores right now", a.top_deals, dealCard));
}

export async function insightsView(ctx) {
  const { page, show } = ctx;
  show(page("Insights", {}, el("div", { class: "skeleton headline-skeleton" })));
  let a;
  try {
    a = await api("/analytics");
  } catch (err) {
    show(page("Insights", {}, el("div", { class: "error-card", text: err.message })), true);
    return;
  }
  if (!/^#\/(insights|analytics)/.test(location.hash)) return; // moved on while loading
  if (!a.totals.tracked) {
    show(page("Insights", { aside: ctx.refreshControl() },
      emptyState("chart", "No numbers yet", "Refresh deals to read every shop. Your insights appear here once prices are in.")), true);
    return;
  }
  show(render(a, ctx), true);
}
