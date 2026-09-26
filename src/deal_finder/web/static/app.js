// Deal Finder front end. No framework: a hash router, a tiny element
// builder, and views rendered from the JSON API.

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const CUR = "AUD";

// ── Helpers ─────────────────────────────────────────────────

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `i ${cls}`);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

function money(value) {
  if (value == null) return "";
  return new Intl.NumberFormat("en-AU", {
    style: "currency", currency: CUR, minimumFractionDigits: value % 1 ? 2 : 0, maximumFractionDigits: 2,
  }).format(value);
}

function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso)) / 6e4);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
}

// Shopify's image CDN resizes on request; ask for card-sized images.
function sized(src, width) {
  const url = safeUrl(src);
  if (!url) return null;
  if (/cdn\.shopify\.com|\/cdn\/shop\//.test(url)) return `${url}${url.includes("?") ? "&" : "?"}width=${width}`;
  return url;
}

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" }, ...opts });
  if (res.status === 401 && path !== "/login") {
    location.href = "/login";
    throw new Error("Sign in first");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail
        : (body.detail || []).map((d) => `${d.loc?.at(-1)}: ${d.msg}`).join("; ") || msg;
    } catch { /* keep statusText */ }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const local = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

function segmented(name, options, value, onchange, cls = "") {
  const node = el("div", { class: `segmented ${cls}`, role: "radiogroup" },
    options.flatMap(([v, label]) => {
      const id = `${name}-${v}`;
      return [
        el("input", { type: "radio", name, id, value: v, checked: String(v) === String(value) }),
        el("label", { for: id, text: label }),
      ];
    }));
  if (onchange) node.addEventListener("change", (e) => onchange(e.target.value));
  return node;
}

function toggle(checked, onchange, label) {
  const input = el("input", { type: "checkbox", role: "switch", "aria-label": label, checked });
  input.addEventListener("change", () => onchange(input.checked));
  return el("label", { class: "switch" }, input, el("span"));
}

// ── App state ───────────────────────────────────────────────

let status = { stores: [], categories: [], settings: { sizes: {} }, size_groups: {} };
let poll = null;

// Shops refresh one request at a time, driven from the page, so each request
// stays well inside a hosted function's time limit.
const updater = { running: false, done: 0, total: 0, current: null };
const isSyncing = () => status.syncing || updater.running;

async function updateShops(keys) {
  if (updater.running || !keys.length) return;
  Object.assign(updater, { running: true, done: 0, total: keys.length });
  for (const key of keys) {
    updater.current = key;
    renderFoot();
    if (location.hash.startsWith("#/stores")) storesView();
    try { await api(`/sync/${encodeURIComponent(key)}`, { method: "POST" }); } catch { /* shown in Stores */ }
    updater.done++;
  }
  Object.assign(updater, { running: false, current: null });
  const wasEmpty = status.empty;
  await refreshStatus();
  if (location.hash.startsWith("#/stores") || wasEmpty) route();
}

function renderFoot() {
  const synced = status.stores.filter((s) => s.synced_at).map((s) => s.synced_at).sort().at(-1);
  const total = status.stores.reduce((a, s) => a + s.products, 0);
  const name = status.stores.find((s) => s.key === updater.current)?.name;
  $("#side-foot").textContent = updater.running ? `Updating ${name} · ${updater.done + 1} of ${updater.total}…`
    : status.syncing ? "Updating shops…"
      : `${total.toLocaleString()} items across ${status.stores.filter((s) => s.products).length} shops · updated ${ago(synced)}`;
}

async function refreshStatus() {
  status = await api("/status");
  const n = await api("/saved").then((s) => s.length).catch(() => 0);
  $("#saved-count").textContent = n || "";
  const cats = $("#side-cats");
  cats.replaceChildren(...status.categories.map((c) =>
    el("a", { class: "side-hunt", href: `#/deals?category=${encodeURIComponent(c)}`, "data-cat": c, text: c })));
  renderFoot();
  markActive();
}

function markActive() {
  const [path, qs] = location.hash.slice(2).split("?");
  const route = path || "deals";
  const cat = new URLSearchParams(qs || "").get("category");
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === route && !(route === "deals" && cat)));
  document.querySelectorAll("[data-cat]").forEach((a) => a.classList.toggle("active", route === "deals" && a.dataset.cat === cat));
}

// ── Page chrome ─────────────────────────────────────────────

function page(title, { narrow = false, actions = [] } = {}, ...content) {
  const nav = el("header", { class: "navbar" },
    el("div", { class: "nav-title", "aria-hidden": "true", text: title }), ...actions);
  return el("div", { class: `page${narrow ? " narrow" : ""}` },
    nav, el("h1", { class: "large-title", text: title }), ...content);
}

function show(node, keepScroll = false) {
  clearInterval(poll);
  const y = window.scrollY;
  view.replaceChildren(node);
  if (keepScroll) { node.style.animation = "none"; window.scrollTo(0, y); } else window.scrollTo(0, 0);
  onScroll();
  markActive();
}

function onScroll() { document.body.classList.toggle("scrolled", window.scrollY > 36); }
window.addEventListener("scroll", onScroll, { passive: true });

function emptyState(iconName, title, text, ...actions) {
  return el("div", { class: "empty" }, icon(iconName), el("h2", { text: title }), el("p", { text }), ...actions);
}

function storageNotice() {
  if (status.persistent !== false) return null;
  return el("div", { class: "notice" },
    el("div", { class: "badge-icon" }, icon("exclaim")),
    el("div", {},
      el("h3", { text: "Add a database to keep your data" }),
      el("p", { style: "margin:0", text: "Without one, prices, price history and saved items are lost whenever Vercel restarts the app. In your Vercel project, open Storage and add Neon Postgres (free), then redeploy." })));
}

function syncingNotice() {
  return el("div", { class: "notice" },
    el("div", { class: "badge-icon", style: "background:var(--blue)" }, icon("refresh")),
    el("div", {},
      el("h3", { text: "Fetching shop catalogues" }),
      el("p", { style: "margin:0", text: "The first update reads every shop’s full range, which takes a couple of minutes. Deals appear as each shop finishes." })));
}

// ── Cards ───────────────────────────────────────────────────

function labelClass(label) { return { "Great deal": "great", "Good deal": "good" }[label] || "fair"; }

function imageWell(d, width = 500) {
  const src = sized(d.image, width);
  const ph = el("div", { class: "placeholder" }, icon("shirt"), el("span", { text: d.category || "" }));
  if (!src) return [ph];
  const img = el("img", { src, alt: "", loading: "lazy", referrerpolicy: "no-referrer", class: "loading" });
  img.addEventListener("load", () => img.classList.remove("loading"));
  img.addEventListener("error", () => img.replaceWith(ph));
  return [img];
}

function badges(d) {
  const cls = { "Just dropped": "drop", "Lowest price seen": "low", "Store-wide sale": "storewide" };
  return (d.badges || []).map((b) => el("span", { class: `badge ${cls[b] || ""}`, text: b }));
}

function offPct(d) { return Math.max(d.discount_pct || 0, d.drop_pct || 0); }

function favButton(d, onchange) {
  let on = Boolean(d.saved);
  const btn = el("button", { class: `fav${on ? " on" : ""}`, "aria-pressed": String(on), "aria-label": on ? "Remove from Saved" : "Save" },
    icon(on ? "heart-fill" : "heart"));
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    try {
      await api(`/saved/${encodeURIComponent(d.id)}`, { method: on ? "DELETE" : "PUT" });
    } catch (err) { toast(err.message); return; }
    on = !on;
    d.saved = on;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.setAttribute("aria-label", on ? "Remove from Saved" : "Save");
    btn.replaceChildren(icon(on ? "heart-fill" : "heart"));
    toast(on ? "Saved. We’ll track its price." : "Removed from Saved");
    refreshStatus();
    onchange?.(on);
  });
  return btn;
}

function priceRow(d) {
  const was = d.was_price || d.prev_price;
  return el("div", { class: "price-row" },
    el("span", { class: `price${offPct(d) ? " sale" : ""}`, text: money(d.price) }),
    was && el("span", { class: "was", text: money(was) }));
}

function dealCard(d, { onopen, selected = false, extra = null } = {}) {
  const pct = offPct(d);
  const card = el("article", {
    class: `card${selected ? " selected" : ""}`, tabindex: 0, role: "button",
    "aria-label": `${d.brand}, ${d.title}, ${money(d.price)}`,
  },
  el("div", { class: "well" }, imageWell(d),
    pct > 0 && el("span", { class: "pill sale", text: `−${pct}%` }),
    favButton(d)),
  el("div", { class: "card-body" },
    el("div", { class: "brand-line" }, el("b", { text: d.brand }), d.brand !== d.store_name && el("span", { text: d.store_name })),
    el("div", { class: "card-name", text: d.title }),
    d.colour && !d.title.toLowerCase().includes(d.colour.toLowerCase().split(/[ /]/)[0]) && el("div", { class: "retailer", text: d.colour }),
    priceRow(d),
    el("div", { class: "card-meta" },
      el("span", { class: `pill ${labelClass(d.label)}`, text: d.label }),
      d.fabric === "natural" && el("span", { class: "retailer", text: "Natural" })),
    d.badges?.length > 0 && el("div", { class: "badges" }, badges(d)),
    extra));
  const open = () => (onopen ? onopen(d) : openProduct(d.id));
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  return card;
}

function skeletonGrid(n = 8) {
  return el("div", { class: "grid" }, Array.from({ length: n }, () => el("div", { class: "skeleton" })));
}

// ── Deals ───────────────────────────────────────────────────

const DEFAULT_FILTERS = {
  q: "", category: [], store: [], min_discount: 0, min_price: "", max_price: "", fabric: "any",
  my_sizes: false, premium_only: false, include_storewide: true, sort: "score",
};
let filters = { ...DEFAULT_FILTERS, ...local.get("df.filters", {}) };

function filterCount() {
  let n = 0;
  if (filters.store.length) n++;
  if (Number(filters.min_discount)) n++;
  if (Number(filters.min_price) > 0 || Number(filters.max_price) > 0) n++;
  if (filters.fabric !== "any") n++;
  if (filters.my_sizes) n++;
  if (filters.premium_only) n++;
  if (!filters.include_storewide) n++;
  return n;
}

function queryString(f, offset = 0) {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  f.category.forEach((c) => p.append("category", c));
  f.store.forEach((s) => p.append("store", s));
  for (const k of ["min_discount", "fabric", "sort"]) if (f[k] !== "" && f[k] != null) p.set(k, f[k]);
  // An empty or zero price means no limit.
  for (const k of ["min_price", "max_price"]) if (Number(f[k]) > 0) p.set(k, Number(f[k]));
  for (const k of ["my_sizes", "premium_only", "include_storewide"]) p.set(k, f[k]);
  p.set("limit", 48);
  p.set("offset", offset);
  return p.toString();
}

async function dealsView(params) {
  const cat = params.get("category");
  filters.category = cat ? [cat] : [];
  local.set("df.filters", { ...filters, category: [] });

  const title = cat || "Deals";
  const grid = el("div", { class: "grid" });
  const meta = el("div", { class: "result-meta" });
  const more = el("div", { class: "load-more" });
  const extraBox = el("div");

  const search = el("input", { type: "search", placeholder: "Search brands, styles, colours", "aria-label": "Search deals", value: filters.q, enterkeyhint: "search" });
  let debounce;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { filters.q = search.value.trim(); local.set("df.filters", { ...filters, category: [] }); load(); }, 250);
  });

  const filterBtn = el("button", { class: "icon-btn filter-btn", "aria-label": "Filters", onclick: () => openFilters(load) }, icon("sliders"));
  const markFilters = () => { const n = filterCount(); if (n) filterBtn.dataset.count = n; else delete filterBtn.dataset.count; };
  markFilters();

  const chips = el("div", { class: "scroller" },
    el("a", { class: "chip", href: "#/deals", "aria-pressed": String(!cat), text: "All" }),
    status.categories.map((c) => el("a", { class: "chip", href: `#/deals?category=${encodeURIComponent(c)}`, "aria-pressed": String(c === cat), text: c })));

  const sortSel = el("select", { "aria-label": "Sort by" },
    [["score", "Best deal"], ["discount", "Biggest discount"], ["price", "Price: low to high"], ["price_desc", "Price: high to low"], ["newest", "Newest"]]
      .map(([v, t]) => el("option", { value: v, text: t, selected: v === filters.sort })));
  sortSel.addEventListener("change", () => { filters.sort = sortSel.value; local.set("df.filters", { ...filters, category: [] }); load(); });

  let offset = 0;
  async function load(append = false) {
    markFilters();
    if (!append) { offset = 0; grid.replaceChildren(...skeletonGrid().children); more.replaceChildren(); }
    let r;
    try {
      r = await api(`/deals?${queryString(filters, offset)}`);
    } catch (err) { grid.replaceChildren(el("div", { class: "error-card", text: err.message })); return; }
    const cards = r.items.map((d) => dealCard(d));
    if (append) grid.append(...cards); else grid.replaceChildren(...cards);
    offset += r.items.length;
    meta.replaceChildren(el("span", { text: `${r.total.toLocaleString()} ${r.total === 1 ? "deal" : "deals"}` }), el("label", { class: "sort" }, "Sort", sortSel));
    more.replaceChildren(offset < r.total ? el("button", { class: "btn", onclick: () => load(true) }, "Show More") : "");
    if (!r.total) {
      grid.replaceChildren(el("div", { style: "grid-column:1/-1" }, status.empty
        ? syncingNotice()
        : emptyState("search", "No deals match", "Try fewer filters, or search other words.",
          filterCount() ? el("button", { class: "btn", onclick: () => { Object.assign(filters, { ...DEFAULT_FILTERS, q: filters.q, sort: filters.sort }); load(); } }, "Clear Filters") : null)));
    }
    renderExtraPrompt();
  }

  function renderExtraPrompt() {
    const q = [filters.q, cat].filter(Boolean).join(" ");
    extraBox.replaceChildren();
    if (!q || !status.extra) return;
    extraBox.append(el("div", { class: "group", style: "margin-top:24px" },
      el("button", { class: "row", style: "width:100%;border:0;background:none;cursor:pointer;text-align:left", onclick: () => runExtra(q, extraBox) },
        el("div", { class: "badge-icon", style: "width:30px;height:30px;border-radius:7px;background:var(--indigo);color:#fff;display:grid;place-items:center" }, icon("search")),
        el("div", { class: "grow" }, el("span", { text: `Search Country Road for “${q}”` }),
          el("span", { class: "sub", text: `Paid search · ${status.extra_used_today} of ${status.extra_cap} used today · cached for a day` })),
        icon("chevron", "chev"))));
  }

  show(page(title, {},
    storageNotice(),
    isSyncing() && status.empty ? syncingNotice() : null,
    el("div", { class: "search-row" }, el("label", { class: "searchbar" }, icon("search"), search), filterBtn),
    chips, meta, grid, more, extraBox));
  load();
  if (isSyncing()) {
    poll = setInterval(async () => {
      await refreshStatus();
      if (!isSyncing()) { clearInterval(poll); load(); }
    }, 5000);
  }
}

async function runExtra(q, box) {
  box.replaceChildren(el("div", { class: "result-meta" }, el("span", { class: "spinner" }), el("span", { text: "Searching other shops…" })));
  try {
    const r = await api(`/extra?q=${encodeURIComponent(q)}`);
    status.extra_used_today = r.used_today;
    box.replaceChildren(
      el("div", { class: "section-title" }, "Other shops", el("span", { class: "retailer", text: r.cached ? "From earlier today" : "Current prices" })),
      r.items.length ? el("div", { class: "group extra-list" }, r.items.map((x) =>
        el("a", { class: "row", href: safeUrl(x.url) || "#", target: "_blank", rel: "noopener noreferrer" },
          sized(x.image, 120) ? el("img", { src: sized(x.image, 120), alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : null,
          el("div", { class: "grow" }, el("div", { class: "t1", text: x.title }), el("div", { class: "t2", text: x.store_name })),
          el("span", { class: "price", text: money(x.price) }), icon("arrow-up-right", "chev"))))
        : el("p", { class: "subtitle", text: "No men’s results from those shops." }),
      el("p", { class: "group-footer", text: "These shops block catalogue reads, so only the current price is known; discounts are not checked." }));
  } catch (err) {
    box.replaceChildren(el("div", { class: "error-card", text: err.message }));
  }
}

function openFilters(onapply) {
  const draft = structuredClone(filters);
  const feeds = status.stores.filter((s) => s.kind !== "search");
  const storeChips = el("div", { class: "chips" }, feeds.map((s) => {
    const chip = el("button", { type: "button", class: "chip", "aria-pressed": String(draft.store.includes(s.key)), text: s.name });
    chip.addEventListener("click", () => {
      draft.store = draft.store.includes(s.key) ? draft.store.filter((k) => k !== s.key) : [...draft.store, s.key];
      chip.setAttribute("aria-pressed", String(draft.store.includes(s.key)));
    });
    return chip;
  }));
  const priceInput = (key, label) => {
    const input = el("input", { type: "number", inputmode: "numeric", min: 0, step: 10, placeholder: "Any", value: Number(draft[key]) > 0 ? draft[key] : "", "aria-label": label });
    input.addEventListener("input", () => { draft[key] = input.value; });
    return input;
  };
  const minPrice = priceInput("min_price", "Minimum price");
  const maxPrice = priceInput("max_price", "Maximum price");
  const sizesSet = Object.values(status.settings.sizes).some(Boolean);

  openSheet("Filters", [
    el("div", { class: "group-label", text: "Discount" }),
    el("div", { class: "group" },
      el("div", { class: "row stack" }, segmented("f-disc", [[0, "Any"], [20, "20%+"], [30, "30%+"], [50, "50%+"]], draft.min_discount, (v) => { draft.min_discount = Number(v); }, "wide")),
      el("div", { class: "row" }, el("div", { class: "grow" }, "Include store-wide sales", el("span", { class: "sub", text: "Shops that mark most of their range down" })),
        toggle(draft.include_storewide, (v) => { draft.include_storewide = v; }, "Include store-wide sales"))),
    el("div", { class: "group-label", text: "Price and fit" }),
    el("div", { class: "group" },
      el("div", { class: "row" }, el("span", { text: "From" }), el("span", { class: "money-field" }, "$", minPrice)),
      el("div", { class: "row" }, el("span", { text: "Up to" }), el("span", { class: "money-field" }, "$", maxPrice)),
      el("div", { class: "row" }, el("div", { class: "grow" }, "In my sizes", el("span", { class: "sub", text: sizesSet ? "Uses the sizes saved in Stores" : "Set your sizes in Stores first" })),
        toggle(draft.my_sizes, (v) => { draft.my_sizes = v; }, "In my sizes"))),
    el("div", { class: "group-label", text: "Fabric" }),
    el("div", { class: "group" }, el("div", { class: "row stack" },
      segmented("f-fab", [["any", "Any"], ["stretch", "Natural + stretch"], ["natural", "Natural only"]], draft.fabric, (v) => { draft.fabric = v; }, "wide"))),
    el("div", { class: "group-label", text: "Shops" }),
    el("div", { class: "group" },
      el("div", { class: "row" }, el("div", { class: "grow" }, "Premium shops only", el("span", { class: "sub", text: "M.J. Bale, P. Johnson, Harrolds, Aquila, Venroy, Bassike, Calibre" })),
        toggle(draft.premium_only, (v) => { draft.premium_only = v; }, "Premium shops only")),
      el("div", { class: "row stack" }, storeChips)),
    el("p", { class: "group-footer", text: "Pick shops to limit results to them. None picked means all." }),
    el("div", { class: "form-actions", style: "display:grid;grid-template-columns:auto 1fr;gap:10px" },
      el("button", { class: "btn", onclick: () => { Object.assign(filters, { ...DEFAULT_FILTERS, q: filters.q, sort: filters.sort, category: filters.category }); local.set("df.filters", { ...filters, category: [] }); closeSheet(); onapply(); } }, "Reset"),
      el("button", { class: "btn primary", onclick: () => { Object.assign(filters, draft); local.set("df.filters", { ...filters, category: [] }); closeSheet(); onapply(); } }, "Show Deals")),
  ]);
}

// ── Product sheet ───────────────────────────────────────────

function sparkline(history, current) {
  const pts = [...history.map((h) => ({ t: new Date(h.at).getTime(), p: h.price, w: h.was_price })), { t: Date.now(), p: current }];
  if (pts.length < 2) return null;
  const W = 300, H = 90, pad = 6;
  const t0 = pts[0].t, t1 = pts.at(-1).t || t0 + 1;
  const prices = pts.flatMap((x) => [x.p, x.w].filter(Boolean));
  const lo = Math.min(...prices) * 0.95, hi = Math.max(...prices) * 1.02;
  const x = (t) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const y = (p) => H - pad - ((p - lo) / Math.max(1, hi - lo)) * (H - 2 * pad);
  // Prices hold until they change, so draw steps.
  let d = `M${x(pts[0].t)},${y(pts[0].p)}`;
  for (let i = 1; i < pts.length; i++) d += ` H${x(pts[i].t)} V${y(pts[i].p)}`;
  const area = `${d} V${H} H${x(pts[0].t)} Z`;
  const was = pts.findLast((q) => q.w)?.w;
  const svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Price history">
    <path class="area" d="${area}"/>${was ? `<line class="was" x1="0" x2="${W}" y1="${y(was)}" y2="${y(was)}"/>` : ""}
    <path class="line" d="${d}"/></svg>`;
  const box = el("div", { class: "spark" });
  box.innerHTML = svg;
  box.append(el("div", { class: "spark-legend" },
    el("span", { text: `Since ${new Date(t0).toLocaleDateString("en-AU", { day: "numeric", month: "short" })}` }),
    el("span", { text: `Low ${money(Math.min(...pts.map((q) => q.p)))} · High ${money(Math.max(...pts.map((q) => q.p)))}` }),
    was ? el("span", { text: `– – was ${money(was)}` }) : null));
  return box;
}

function mySizesFor(category) {
  const group = Object.entries(status.size_groups).find(([, cats]) => cats.includes(category))?.[0];
  return (status.settings.sizes[group] || "").split(/[,/]/).map((s) => s.trim().toLowerCase()).filter(Boolean);
}

async function openProduct(id) {
  let d;
  try { d = await api(`/products/${encodeURIComponent(id)}`); } catch (err) { toast(err.message); return; }
  const url = safeUrl(d.url);
  const pct = offPct(d);
  const ringColour = d.score >= 75 ? "var(--green)" : d.score >= 55 ? "var(--blue)" : "var(--label-3)";
  const ring = el("div", { class: "ring", style: `--p:0;--c:${ringColour}` });
  ring.innerHTML = '<svg viewBox="0 0 40 40"><circle class="track" cx="20" cy="20" r="15.9" pathLength="100"/><circle class="bar" cx="20" cy="20" r="15.9" pathLength="100"/></svg>';
  ring.append(el("b", { text: d.score }));
  requestAnimationFrame(() => requestAnimationFrame(() => ring.style.setProperty("--p", d.score)));

  const fav = favButton(d);
  fav.className = `btn${d.saved ? " on" : ""}`;
  fav.style.position = "static";
  const copy = el("button", { class: "btn", "aria-label": "Copy link", title: "Copy link" }, icon("link"));
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(d.url); toast("Link copied"); } catch { toast("Couldn’t copy"); }
  });

  const mine = mySizesFor(d.category);
  const fabricText = { natural: "Natural fibres", stretch: "Natural with stretch", synthetic: "Contains synthetics", unknown: "Not listed" }[d.fabric];
  const facts = [
    ["Shop", d.store_name],
    ["Colour", d.colour],
    ["Fabric", [d.composition, fabricText].filter(Boolean).join(" · ")],
    d.saved_price != null && ["Price when saved", money(d.saved_price)],
    ["Tracking since", d.first_seen ? new Date(d.first_seen).toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" }) : "—"],
  ].filter((f) => f && f[1]);

  const why = [
    d.discount_pct && `${d.discount_pct}% off the shop’s “was” price${d.badges.includes("Store-wide sale") ? " (counted at half: this shop marks most of its range down)" : ""}`,
    d.drop_pct && `dropped ${d.drop_pct}% since we last checked`,
    d.badges.includes("Lowest price seen") && "the lowest price we’ve recorded",
    d.fabric === "natural" && "natural fibres",
    d.tier === "premium" && "a premium shop",
  ].filter(Boolean);

  openSheet(null, [
    el("div", { class: "detail-hero" },
      el("div", { class: "well" }, imageWell(d, 900), pct > 0 && el("span", { class: "pill sale", text: `−${pct}%` })),
      el("div", {},
        el("div", { class: "brand-line" }, el("b", { text: d.brand }), d.brand !== d.store_name && el("span", { text: `at ${d.store_name}` })),
        el("h2", { id: "sheet-title", text: d.title }),
        el("div", { class: "detail-price" },
          el("span", { class: `price${pct ? " sale" : ""}`, text: money(d.price) }),
          (d.was_price || d.prev_price) && el("span", { class: "was", text: money(d.was_price || d.prev_price) }),
          el("span", { class: `pill ${labelClass(d.label)}`, text: d.label })),
        d.badges.length > 0 && el("div", { class: "badges", style: "margin:-6px 0 12px" }, badges(d)),
        el("div", { class: "score-line" }, ring,
          el("div", {}, el("div", { class: "k", text: "Deal score" }),
            el("div", { class: "v", text: d.saved_price != null && d.price !== d.saved_price
              ? `${d.price < d.saved_price ? "Down" : "Up"} ${money(Math.abs(d.price - d.saved_price))} since you saved it` : `You save ${money(d.saving)}` }))),
        el("div", { class: "detail-actions" },
          url ? el("a", { class: "btn primary", href: url, target: "_blank", rel: "noopener noreferrer" }, `Buy at ${d.store_name}`, icon("arrow-up-right")) : el("span"),
          fav, copy))),
    why.length > 0 && el("div", { class: "quote" }, el("b", { text: "Why this score" }), `${why.join(", ")}.`.replace(/^./, (c) => c.toUpperCase())),
    d.history.length > 0 && el("div", { class: "group-label", text: "Price history" }),
    d.history.length > 0 && el("div", { class: "group" }, sparkline(d.history, d.price)
      || el("div", { class: "row", text: "We’ll chart this price as it changes." })),
    d.sizes.length > 0 && el("div", { class: "group-label", text: "Sizes in stock" }),
    d.sizes.length > 0 && el("div", { class: "group" }, el("div", { class: "size-chips" },
      d.sizes.map((s) => el("span", { class: mine.includes(s.toLowerCase()) ? "mine" : "", text: s })))),
    el("div", { class: "group-label", text: "Details" }),
    el("div", { class: "group" }, facts.map(([k, v]) => el("div", { class: "row" }, el("span", { text: k }), el("span", { class: "value", text: v })))),
  ]);
}

// ── Sheet ───────────────────────────────────────────────────

let lastFocus = null;

function openSheet(title, content) {
  const sheet = $("#sheet");
  const backdrop = $("#sheet-backdrop");
  lastFocus = document.activeElement;
  const close = el("button", { class: "close-btn", "aria-label": "Close", onclick: closeSheet }, icon("xmark"));
  sheet.replaceChildren(
    el("div", { class: "grabber" }),
    title ? el("div", { class: "sheet-bar" }, el("h2", { id: "sheet-title", text: title }), close) : el("div", { class: "sheet-head" }, close),
    ...content.filter(Boolean));
  sheet.hidden = false;
  backdrop.hidden = false;
  sheet.classList.remove("closing");
  backdrop.classList.remove("closing");
  document.body.style.overflow = "hidden";
  sheet.scrollTop = 0;
  close.focus();
}

function closeSheet() {
  const sheet = $("#sheet");
  const backdrop = $("#sheet-backdrop");
  if (sheet.hidden) return;
  sheet.classList.add("closing");
  backdrop.classList.add("closing");
  document.body.style.overflow = "";
  setTimeout(() => { sheet.hidden = true; backdrop.hidden = true; lastFocus?.focus?.(); }, 250);
}
$("#sheet-backdrop").addEventListener("click", closeSheet);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

// ── Outfit ──────────────────────────────────────────────────

const IDEAS = ["Smart casual dinner", "Office, navy and grey", "Summer wedding", "Weekend, relaxed", "Winter layers", "Navy blazer, white shirt, brown loafers"];

function outfitView() {
  const last = local.get("df.outfit", {});
  const form = { brief: "", budget: 600, use_ai: false, my_sizes: true, premium_only: false, fabric: "any", include_storewide: true, ...last };

  const brief = el("textarea", { rows: 3, maxlength: 400, "aria-label": "Describe what you need", placeholder: "What’s it for? e.g. smart casual dinner, navy and stone. Or list pieces: navy blazer, white shirt, brown loafers" });
  brief.value = form.brief;
  const ideas = el("div", { class: "chips" }, IDEAS.map((i) => el("button", { type: "button", class: "chip", text: i, onclick: () => { brief.value = i; brief.focus(); } })));

  const budget = el("input", { type: "number", min: 50, max: 20000, step: 10, inputmode: "numeric", "aria-label": "Budget", value: form.budget });
  const slider = el("input", { type: "range", min: 100, max: 3000, step: 10, "aria-label": "Budget slider" });
  const sync = () => { slider.value = Math.min(3000, Math.max(100, Number(budget.value) || 100)); slider.style.setProperty("--pct", `${((slider.value - 100) / 2900) * 100}%`); };
  slider.addEventListener("input", () => { budget.value = slider.value; sync(); });
  budget.addEventListener("input", sync);
  sync();

  const results = el("div");
  const go = el("button", { type: "submit", class: "btn primary large" }, icon("sparkles"), "Build Outfit");

  const formEl = el("form", {},
    el("div", { class: "composer" }, brief, ideas),
    el("div", { class: "group-label", text: "Budget" }),
    el("div", { class: "group" },
      el("div", { class: "row" }, el("span", { text: "Total to spend" }), el("span", { class: "money-field" }, "$", budget)),
      el("div", { class: "row" }, slider)),
    el("div", { class: "group-label", text: "Options" }),
    el("div", { class: "group" },
      el("div", { class: "row" }, el("div", { class: "grow" }, "In my sizes", el("span", { class: "sub", text: "Set sizes in Stores" })), toggle(form.my_sizes, (v) => { form.my_sizes = v; }, "In my sizes")),
      el("div", { class: "row" }, el("span", { class: "grow", text: "Premium shops only" }), toggle(form.premium_only, (v) => { form.premium_only = v; }, "Premium shops only")),
      el("div", { class: "row" }, el("div", { class: "grow" }, "Read my brief with AI",
        el("span", { class: "sub", text: status.ai ? "One small Claude call, under a cent; repeats are free" : "Add ANTHROPIC_API_KEY or OPENROUTER_API_KEY to .env to turn on" })),
      toggle(form.use_ai && status.ai, (v) => { form.use_ai = v; }, "Read my brief with AI")),
      el("div", { class: "row stack" }, el("span", { class: "row-label", text: "Fabric" }),
        segmented("o-fab", [["any", "Any"], ["stretch", "Natural + stretch"], ["natural", "Natural only"]], form.fabric, (v) => { form.fabric = v; }))),
    el("p", { class: "group-footer", text: "Without AI, looks like “office” or “summer” use a preset, and lists like “navy blazer, white shirt” are read word by word. Both are free." }),
    el("div", { class: "form-actions" }, go));
  if (!status.ai) formEl.querySelector('[aria-label="Read my brief with AI"]').disabled = true;

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { ...form, brief: brief.value.trim(), budget: Number(budget.value), use_ai: form.use_ai && status.ai };
    if (body.brief.length < 2) { brief.focus(); toast("Describe what you need first"); return; }
    local.set("df.outfit", body);
    go.disabled = true;
    results.replaceChildren(el("div", { class: "result-meta" }, el("span", { class: "spinner" }), el("span", { text: body.use_ai ? "Reading your brief…" : "Picking pieces…" })));
    results.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      renderOutfit(await api("/outfit", { method: "POST", body: JSON.stringify(body) }), results);
    } catch (err) {
      results.replaceChildren(el("div", { class: "error-card", text: err.message }));
    }
    go.disabled = false;
  });

  show(page("Outfit", { narrow: true },
    el("p", { class: "subtitle", text: "Describe a look and a budget. You get the best-value piece for each part of it, with alternatives." }),
    storageNotice(), status.empty ? syncingNotice() : null, formEl, results));
}

function renderOutfit(r, box) {
  const picks = r.slots.map((s) => s.pick);
  const summary = el("div", { class: "summary" });
  const drawSummary = () => {
    const chosen = picks.filter(Boolean);
    const total = chosen.reduce((a, d) => a + d.price, 0);
    const saved = chosen.reduce((a, d) => a + d.saving, 0);
    summary.replaceChildren(
      el("div", {},
        el("div", { class: "k", text: r.plan.look }),
        el("div", { class: "v" }, money(total), el("small", { text: ` of ${money(r.budget)}` })),
        el("div", { class: "meter" }, el("i", { style: `width:${Math.min(100, (total / r.budget) * 100)}%;${total > r.budget ? "background:var(--red)" : ""}` })),
        el("div", { class: "meter-caption", text: total > r.budget ? `${money(total - r.budget)} over budget` : `${money(r.budget - total)} left · ${chosen.length} pieces` })),
      el("div", {}, el("div", { class: "k", text: "You save" }), el("div", { class: `v${saved ? " green" : ""}`, text: money(saved) })),
      el("div", {}, el("div", { class: "k", text: "Read by" }), el("div", { class: "v", style: "font-size:22px", text: { ai: "AI", preset: "Preset", keywords: "Your list" }[r.plan.source] })));
  };
  drawSummary();

  const slots = r.slots.map((s, i) => {
    const body = el("div", { class: "slot-body" });
    const draw = () => {
      const pick = picks[i];
      const options = [s.pick, ...s.alternates].filter(Boolean).filter((d, j, a) => a.findIndex((x) => x.id === d.id) === j);
      body.replaceChildren(
        pick ? dealCard(pick) : el("div", { class: "error-card", text: "Nothing fits the budget and sizes. Alternatives are on the right." }),
        options.length > 1 ? el("div", {},
          el("div", { class: "alts-label", text: "Tap to swap" }),
          el("div", { class: "alts" }, options.map((d) => dealCard(d, {
            selected: pick && d.id === pick.id,
            onopen: () => { picks[i] = d; draw(); drawSummary(); },
          })))) : null);
    };
    draw();
    const words = [...s.slot.colours, ...s.slot.keywords].slice(0, 4).join(", ");
    return el("section", { class: "slot" },
      el("div", { class: "slot-head" }, el("h3", { text: s.slot.category }), el("span", { text: words })), body);
  });

  const extraBox = el("div");
  box.replaceChildren(
    el("div", { class: "section-title", text: "Your outfit" }),
    summary, ...slots,
    status.extra && el("div", { class: "group", style: "margin-top:28px" },
      el("button", { class: "row", style: "width:100%;border:0;background:none;cursor:pointer;text-align:left", onclick: () => runExtra(`${r.plan.look} ${r.slots.map((s) => s.slot.category).slice(0, 2).join(" ")}`, extraBox) },
        el("span", { class: "grow", text: "Also check Country Road" }), icon("chevron", "chev"))),
    extraBox);
}

// ── Saved ───────────────────────────────────────────────────

async function savedView() {
  const items = await api("/saved");
  if (!items.length) {
    show(page("Saved", {}, emptyState("heart", "Nothing saved yet", "Tap the heart on any piece. We’ll track its price and show you when it drops.",
      el("a", { class: "btn primary", href: "#/deals" }, "Browse Deals"))));
    return;
  }
  const down = items.filter((d) => d.change < 0);
  const cards = items.map((d) => dealCard(d, {
    extra: d.change ? el("div", { class: `change ${d.change < 0 ? "down" : "up"}`, style: "margin-top:6px;font-size:13px",
      text: `${d.change < 0 ? "↓" : "↑"} ${money(Math.abs(d.change))} since saved` })
      : !d.active ? el("div", { class: "reason", text: "No longer listed" }) : null,
  }));
  show(page("Saved", {},
    el("p", { class: "subtitle", text: down.length ? `${down.length} of ${items.length} have dropped in price since you saved them.` : `${items.length} saved. We check prices every 12 hours.` }),
    el("div", { class: "grid" }, cards)));
}

// ── Stores ──────────────────────────────────────────────────

function storesView() {
  const labels = { tops: "Tops", bottoms: "Waist", tailoring: "Jackets & suits", shoes: "Shoes" };
  const hints = { tops: "M, L", bottoms: "32, 82", tailoring: "40, 100, M", shoes: "9, 42, UK 9" };
  const inputs = Object.keys(labels).map((g) => {
    const input = el("input", { type: "text", id: `size-${g}`, placeholder: hints[g], value: status.settings.sizes[g] || "", maxlength: 60 });
    return [g, input];
  });
  const saveSizes = async () => {
    const sizes = Object.fromEntries(inputs.map(([g, i]) => [g, i.value.trim()]));
    status.settings = await api("/settings", { method: "PUT", body: JSON.stringify({ sizes }) });
    toast("Sizes saved");
  };
  inputs.forEach(([, i]) => i.addEventListener("change", saveSizes));

  const feeds = status.stores.filter((s) => s.kind !== "search");
  const searched = status.stores.filter((s) => s.kind === "search");
  const syncBtn = el("button", { class: "text-btn", disabled: isSyncing(), onclick: () => {
    updateShops(feeds.map((s) => s.key));
    toast("Updating every shop…");
  } }, icon("refresh"), isSyncing() ? "Updating…" : "Update Now");

  show(page("Stores", { narrow: true, actions: [syncBtn] },
    el("p", { class: "subtitle", text: "Deal Finder reads these shops’ public catalogues for free and records every price change." }),
    el("div", { class: "group-label", text: "My sizes" }),
    el("div", { class: "group" }, inputs.map(([g, input]) =>
      el("div", { class: "row" }, el("label", { for: input.id, text: labels[g] }), input))),
    el("p", { class: "group-footer", text: "List every size you wear in each shop’s system, separated by commas. Jackets come in 40, 100 or M depending on the shop." }),
    el("div", { class: "group-label", text: `Catalogues · free` }),
    el("div", { class: "group" }, feeds.map((s) =>
      el("div", { class: "row" },
        s.syncing || updater.current === s.key ? el("span", { class: "spinner" }) : el("span", { class: `store-dot${s.ok === false ? " bad" : s.ok == null ? " never" : ""}` }),
        el("div", { class: "grow" },
          el("span", { text: s.name }),
          el("span", { class: "sub", text: s.ok === false ? `Couldn’t update: ${s.error || "unknown error"}`
            : `${s.products.toLocaleString()} items · ${s.on_sale.toLocaleString()} marked down${s.storewide ? " (store-wide)" : ""} · ${ago(s.synced_at)}` })),
        el("span", { class: "value", style: "font-size:13px", text: s.tier === "premium" ? "Premium" : "Mid" })))),
    el("div", { class: "group-label", text: "Searched on request · paid" }),
    el("div", { class: "group" }, searched.map((s) =>
      el("div", { class: "row" }, el("span", { class: "grow", text: s.name }), el("span", { class: "value", style: "font-size:13px", text: status.extra ? "Ready" : "Needs key" })))),
    el("p", { class: "group-footer", text: status.extra
      ? `These shops block catalogue reads. Search them from Deals or Outfit through Serper, about A$0.002 a search. ${status.extra_used_today} of ${status.extra_cap} searches used today.`
      : "These shops block catalogue reads. Add SERPER_API_KEY to .env to search them on request (free for the first 2,500 searches)." }),
    el("div", { class: "group-label", text: "AI" }),
    el("div", { class: "group" }, el("div", { class: "row" }, el("span", { class: "grow", text: "Read briefs with Claude" }),
      el("span", { class: "value", style: "font-size:13px", text: status.ai ? status.ai_model : "Needs ANTHROPIC_API_KEY or OPENROUTER_API_KEY" }))),
    status.signed_in && el("div", { class: "group", style: "margin-top:28px" },
      el("button", { class: "row", style: "width:100%;border:0;background:none;cursor:pointer;color:var(--red);font-size:17px",
        onclick: async () => { await api("/logout", { method: "POST" }); location.href = "/login"; } }, "Sign Out")),
  ));
  if (status.syncing) {
    poll = setInterval(async () => {
      await refreshStatus();
      if (location.hash.startsWith("#/stores")) storesView();
      if (!status.syncing) clearInterval(poll);
    }, 3000);
  }
}

// ── Router ──────────────────────────────────────────────────

async function route() {
  closeSheet();
  const [path, qs] = location.hash.slice(2).split("?");
  const params = new URLSearchParams(qs || "");
  if (path === "outfit") return outfitView();
  if (path === "saved") return savedView();
  if (path === "stores") return storesView();
  return dealsView(params);
}

window.addEventListener("hashchange", route);

(async function start() {
  try {
    await refreshStatus();
  } catch (err) {
    view.replaceChildren(emptyState("exclaim", "Can’t reach Deal Finder", err.message));
    return;
  }
  if (!location.hash) history.replaceState(null, "", "#/deals");
  route();
  // Hosted: nothing runs between visits except the daily cron, so opening the
  // app refreshes any shop older than 12 hours. Locally the server does this.
  if (status.hosted && !status.syncing) {
    updateShops(status.stores.filter((s) => s.kind !== "search" && s.stale).map((s) => s.key));
  }
})();
