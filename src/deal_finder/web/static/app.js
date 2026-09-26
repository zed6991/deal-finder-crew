// Deal Finder front end. No framework: a hash router, a tiny element
// builder, and views that re-render from JSON the API returns.

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");

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

function money(value, currency) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency", currency, minimumFractionDigits: value % 1 ? 2 : 0, maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${currency} ${Number(value).toFixed(2)}`;
  }
}

function when(iso) {
  const d = new Date(iso);
  const days = Math.round((Date.now() - d) / 864e5);
  if (days < 1) return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
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

const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  },
};

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

// ── Saved deals (kept in this browser) ─────────────────────

const saved = {
  all: () => store.get("df.saved", {}),
  has: (url) => url in saved.all(),
  toggle(deal, currency) {
    const all = saved.all();
    if (all[deal.url]) delete all[deal.url];
    else all[deal.url] = { ...deal, currency: deal.currency || currency, saved_at: Date.now() };
    store.set("df.saved", all);
    updateSavedCount();
    return Boolean(all[deal.url]);
  },
};

function updateSavedCount() {
  const n = Object.keys(saved.all()).length;
  $("#saved-count").textContent = n || "";
}

// ── App state ───────────────────────────────────────────────

let config = { regions: [], categories: [], missing_keys: [] };
let hunts = [];
let poll = null;

async function refreshHunts() {
  hunts = await api("/hunts");
  const recent = $("#recent");
  recent.replaceChildren(
    ...hunts.slice(0, 8).map((h) =>
      el("a", { class: "side-hunt", href: `#/hunt/${h.id}`, "data-hunt": h.id },
        h.brief, el("small", { text: h.status === "done" ? `${h.picks} picks · ${when(h.created_at)}` : statusText(h.status) }))),
  );
  markActive();
}

function statusText(s) {
  return { queued: "Waiting…", running: "Hunting…", failed: "Didn’t finish", done: "Done" }[s] || s;
}

function markActive() {
  const [, route, id] = location.hash.split("/");
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === (route || "new")));
  document.querySelectorAll("[data-hunt]").forEach((a) =>
    a.classList.toggle("active", route === "hunt" && a.dataset.hunt === id));
}

// ── Chrome shared by every page ─────────────────────────────

function page(title, { narrow = false, back = null, actions = [] } = {}, ...content) {
  const nav = el("header", { class: "navbar" },
    back && el("a", { class: "text-btn back", href: back.href }, icon("chevron", "flip"), back.label),
    el("div", { class: "nav-title", "aria-hidden": "true", text: title }),
    ...actions);
  const flip = nav.querySelector(".flip");
  if (flip) flip.style.transform = "scaleX(-1)";
  return el("div", { class: `page${narrow ? " narrow" : ""}` },
    nav, el("h1", { class: "large-title", text: title }), ...content);
}

function show(node) {
  clearInterval(poll);
  view.replaceChildren(node);
  window.scrollTo(0, 0);
  onScroll();
  markActive();
}

function onScroll() {
  document.body.classList.toggle("scrolled", window.scrollY > 36);
}
window.addEventListener("scroll", onScroll, { passive: true });

// ── New hunt ────────────────────────────────────────────────

const IDEAS = ["Smart casual for the office", "Summer linen", "Weekend knitwear",
  "Wedding guest, spring", "Minimal black and navy", "Rugged workwear"];

function segmented(name, options, value, cls = "") {
  return el("div", { class: `segmented ${cls}`, role: "radiogroup" },
    options.flatMap(([v, label]) => {
      const id = `${name}-${v}`;
      return [
        el("input", { type: "radio", name, id, value: v, checked: String(v) === String(value) }),
        el("label", { for: id, text: label }),
      ];
    }));
}

function newHuntView() {
  const last = store.get("df.form", {});
  const form = {
    brief: last.brief || "", budget: last.budget || 400, region: last.region || "au",
    sizes: last.sizes || "", categories: last.categories || [], fabric: last.fabric || "natural",
    min_discount: last.min_discount ?? 0, notes: last.notes || "",
  };
  const region = () => config.regions.find((r) => r.code === form.region) || config.regions[0];

  const brief = el("textarea", {
    id: "brief", rows: 3, maxlength: 500, "aria-label": "Describe the look you want",
    placeholder: "Describe the look… e.g. smart casual for a 30 year old, navy and stone",
  });
  brief.value = form.brief;

  const ideaChips = el("div", { class: "chips" }, IDEAS.map((idea) =>
    el("button", { type: "button", class: "chip", text: idea, onclick: () => { brief.value = idea; brief.focus(); } })));

  const budgetIn = el("input", { type: "number", id: "budget", min: 20, max: 100000, step: 10, inputmode: "numeric", "aria-label": "Budget" });
  budgetIn.value = form.budget;
  const symbol = el("span", { text: region()?.symbol || "$" });
  const slider = el("input", { type: "range", min: 50, max: 2000, step: 10, "aria-label": "Budget slider" });
  const syncSlider = () => {
    slider.value = Math.min(2000, Math.max(50, Number(budgetIn.value) || 50));
    slider.style.setProperty("--pct", `${((slider.value - 50) / 1950) * 100}%`);
  };
  slider.addEventListener("input", () => { budgetIn.value = slider.value; syncSlider(); });
  budgetIn.addEventListener("input", syncSlider);
  syncSlider();

  const regionSel = el("select", { id: "region", "aria-label": "Shopping region" },
    config.regions.map((r) => el("option", { value: r.code, text: `${r.name} (${r.currency})`, selected: r.code === form.region })));
  regionSel.addEventListener("change", () => { form.region = regionSel.value; symbol.textContent = region().symbol; });

  const sizes = el("input", { type: "text", id: "sizes", placeholder: "M, 32 waist, UK 9", maxlength: 200 });
  sizes.value = form.sizes;
  const notes = el("input", { type: "text", id: "notes", placeholder: "Optional", maxlength: 500 });
  notes.value = form.notes;

  const cats = new Set(form.categories);
  const catChips = el("div", { class: "chips" }, config.categories.map((c) => {
    const chip = el("button", { type: "button", class: "chip", "aria-pressed": String(cats.has(c)), text: c });
    chip.addEventListener("click", () => {
      cats.has(c) ? cats.delete(c) : cats.add(c);
      chip.setAttribute("aria-pressed", String(cats.has(c)));
    });
    return chip;
  }));

  const fabric = segmented("fabric", [["natural", "Natural only"], ["stretch", "Stretch OK"], ["any", "Any"]], form.fabric);
  const discount = segmented("discount", [[0, "Any"], [20, "20%+"], [30, "30%+"], [50, "50%+"]], form.min_discount);

  const submit = el("button", { type: "submit", class: "btn primary large" }, icon("sparkles"), "Find Deals");
  const missing = config.missing_keys;

  const formEl = el("form", { novalidate: true },
    el("div", { class: "composer" }, el("label", { for: "brief", class: "sr-only", text: "Style brief" }), brief, ideaChips),

    el("div", { class: "group-label", text: "Budget" }),
    el("div", { class: "group" },
      el("div", { class: "row" }, el("label", { for: "budget", text: "Total to spend" }),
        el("span", { class: "money-field" }, symbol, budgetIn)),
      el("div", { class: "row" }, slider)),

    el("div", { class: "group-label", text: "About you" }),
    el("div", { class: "group" },
      el("div", { class: "row" }, el("label", { for: "region", text: "Shop in" }), regionSel),
      el("div", { class: "row" }, el("label", { for: "sizes", text: "Sizes" }), sizes),
      el("div", { class: "row" }, el("label", { for: "notes", text: "Notes" }), notes)),
    el("p", { class: "group-footer", text: "Sizes and notes guide the search, e.g. “no logos” or “slim fit”." }),

    el("div", { class: "group-label", text: "Rules" }),
    el("div", { class: "group" },
      el("div", { class: "row stack" }, el("span", { class: "row-label", text: "Fabric" }), fabric),
      el("div", { class: "row stack" }, el("span", { class: "row-label", text: "Minimum discount" }), discount)),
    el("p", { class: "group-footer", text: "Natural only rules out polyester, nylon, viscose and the like. Stretch OK allows up to 5% elastane." }),

    el("div", { class: "group-label", text: "Categories" }),
    el("div", { class: "group" }, el("div", { class: "row stack" }, catChips)),
    el("p", { class: "group-footer", text: "Leave all off and the stylist will choose." }),

    el("div", { class: "form-actions" }, submit),
  );

  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      brief: brief.value.trim(),
      budget: Number(budgetIn.value),
      region: regionSel.value,
      sizes: sizes.value.trim(),
      notes: notes.value.trim(),
      categories: [...cats],
      fabric: formEl.fabric.value,
      min_discount: Number(formEl.discount.value),
    };
    if (body.brief.length < 3) { brief.focus(); toast("Describe the look you want first"); return; }
    if (!(body.budget > 0)) { budgetIn.focus(); toast("Set a budget"); return; }
    store.set("df.form", body);
    submit.disabled = true;
    try {
      const hunt = await api("/hunts", { method: "POST", body: JSON.stringify(body) });
      await refreshHunts();
      location.hash = `#/hunt/${hunt.id}`;
    } catch (err) {
      toast(err.message);
      submit.disabled = false;
    }
  });

  const notice = missing.length > 0 && el("div", { class: "notice" },
    el("div", { class: "badge-icon" }, icon("key")),
    el("div", {},
      el("h3", { text: "Add your API keys to start hunting" }),
      el("p", {}, "Put ", missing.map((k, i) => [i ? ", " : "", el("code", { text: k.split(" ")[0] })]),
        " in the ", el("code", { text: ".env" }), " file, then restart the app. Meanwhile, you can explore a sample hunt."),
      el("button", { type: "button", class: "btn", onclick: openSample }, "Open Sample Hunt")));

  show(page("Find Deals", { narrow: true },
    el("p", { class: "subtitle", text: "Describe a look. A stylist plans it, a hunter finds it on sale, and every price is checked." }),
    notice, formEl));
}

async function openSample() {
  const h = await api("/hunts/sample", { method: "POST" });
  await refreshHunts();
  location.hash = `#/hunt/${h.id}`;
}

// ── Hunt list ───────────────────────────────────────────────

function huntsView() {
  if (!hunts.length) {
    show(page("Hunts", { narrow: true }, el("div", { class: "empty" },
      icon("clock"), el("h2", { text: "No hunts yet" }),
      el("p", { text: "Your searches and their results will be kept here." }),
      el("a", { class: "btn primary", href: "#/new" }, "Start a Hunt"),
      config.missing_keys.length ? el("div", { style: "margin-top:12px" },
        el("button", { class: "text-btn", onclick: openSample, text: "Or open the sample hunt" })) : null)));
    return;
  }
  show(page("Hunts", { narrow: true },
    el("div", { class: "group", style: "margin-top:12px" }, hunts.map((h) =>
      el("a", { class: "row hunt-row", href: `#/hunt/${h.id}` },
        el("div", { class: `thumb${h.sample ? " sample" : ""}` }, icon(h.sample ? "sparkles" : "tag")),
        el("div", { class: "text" },
          el("div", { class: "t1", text: h.brief }),
          el("div", { class: "t2" }, h.status === "done"
            ? `${h.picks} picks · ${money(h.total, h.currency)}${h.savings ? ` · saved ${money(h.savings, h.currency)}` : ""}`
            : el("span", { class: `status ${h.status}`, text: statusText(h.status) }))),
        el("span", { class: "value", style: "font-size:15px", text: h.sample ? "Sample" : when(h.created_at) }),
        icon("chevron", "chev")))),
  ));
}

// ── One hunt ────────────────────────────────────────────────

let huntUi = { tab: "picks", sort: "score" };

async function huntView(id) {
  let hunt;
  try {
    hunt = await api(`/hunts/${id}`);
  } catch {
    show(page("Not found", { narrow: true }, el("div", { class: "empty" },
      icon("exclaim"), el("h2", { text: "This hunt is gone" }),
      el("a", { class: "btn primary", href: "#/hunts" }, "See All Hunts"))));
    return;
  }
  if (hunt.status === "done") return resultsView(hunt);

  renderProgress(hunt);
  if (hunt.status === "failed") return;
  poll = setInterval(async () => {
    if (location.hash !== `#/hunt/${id}`) return clearInterval(poll);
    try {
      const next = await api(`/hunts/${id}`);
      if (next.status === "done" || next.status === "failed") {
        clearInterval(poll);
        await refreshHunts();
        if (next.status === "done") toast("Your deals are ready");
        return next.status === "done" ? resultsView(next) : renderProgress(next);
      }
      renderProgress(next, true);
    } catch { /* try again next tick */ }
  }, 1500);
}

function renderProgress(hunt, keepScroll = false) {
  const p = hunt.progress || {};
  const stages = p.stages || ["Planning the outfit", "Searching shops", "Checking prices", "Judging style and value"];
  const stage = hunt.status === "queued" ? -1 : p.stage ?? 0;
  const failed = hunt.status === "failed";

  const steps = el("ol", { class: "steps group" }, stages.map((label, i) => {
    const state = i < stage ? "done" : i === stage && !failed ? "now" : "todo";
    return el("li", { class: `row step ${state}` },
      el("span", { class: "dot" }, state === "done" ? icon("check") : null),
      el("span", { text: label }));
  }));

  const events = (p.events || []).slice(-8).reverse();
  const content = [
    el("p", { class: "subtitle", text: failed ? "This hunt didn’t finish." : hunt.status === "queued" ? "Waiting for the hunt ahead to finish…" : "This takes a few minutes. You can leave this page; the hunt keeps going." }),
    failed && el("div", { class: "error-card", text: hunt.error || "Something went wrong." }),
    failed && el("div", { class: "form-actions" }, el("button", {
      class: "btn primary large", onclick: () => retry(hunt),
    }, "Try Again")),
    el("div", { class: "group-label", text: "Progress" }), steps,
    el("div", { class: "stats" },
      el("div", { class: "stat" }, el("b", { text: p.searches || 0 }), el("span", { text: "searches" })),
      el("div", { class: "stat" }, el("b", { text: p.pages || 0 }), el("span", { text: "pages checked" }))),
    events.length > 0 && el("div", { class: "group-label", text: "Activity" }),
    events.length > 0 && el("div", { class: "group feed" }, events.map((e) =>
      el("div", { class: "row" }, icon(e.kind === "search" ? "sparkles" : "link"), el("span", { text: e.text })))),
  ];

  const node = page(hunt.request.brief, {
    narrow: true, back: { href: "#/hunts", label: "Hunts" },
    actions: [el("button", { class: "icon-btn danger", "aria-label": "Delete hunt", onclick: () => removeHunt(hunt) }, icon("trash"))],
  }, ...content);

  if (keepScroll) {
    const y = window.scrollY;
    view.replaceChildren(node);
    node.style.animation = "none";
    window.scrollTo(0, y);
  } else {
    show(node);
  }
}

async function retry(hunt) {
  try {
    const h = await api("/hunts", { method: "POST", body: JSON.stringify(hunt.request) });
    await refreshHunts();
    location.hash = `#/hunt/${h.id}`;
  } catch (err) { toast(err.message); }
}

async function removeHunt(hunt) {
  if (!confirm(`Delete “${hunt.request.brief}”?`)) return;
  await api(`/hunts/${hunt.id}`, { method: "DELETE" });
  await refreshHunts();
  toast("Hunt deleted");
  location.hash = "#/hunts";
}

const SORTS = {
  score: (a, b) => b.score - a.score || a.price - b.price,
  discount: (a, b) => b.discount_pct - a.discount_pct || b.score - a.score,
  price: (a, b) => a.price - b.price,
  savings: (a, b) => b.savings - a.savings,
};

function resultsView(hunt) {
  const r = hunt.report;
  const cur = r.currency;
  const regionName = config.regions.find((x) => x.code === hunt.request.region)?.name || "";
  const picks = r.deals.filter((d) => d.picked);
  const eligible = r.deals.filter((d) => d.eligible);
  const passed = r.deals.filter((d) => !d.eligible);
  const pct = Math.min(100, (r.total / r.budget) * 100);

  const summary = el("div", { class: "summary" },
    el("div", {},
      el("div", { class: "k", text: "Your picks" }),
      el("div", { class: "v" }, money(r.total, cur), el("small", { text: ` of ${money(r.budget, cur)}` })),
      el("div", { class: "meter", role: "meter", "aria-valuemin": 0, "aria-valuemax": r.budget, "aria-valuenow": r.total, "aria-label": "Budget used" },
        el("i", { style: "width:0%" })),
      el("div", { class: "meter-caption", text: `${money(r.remaining, cur)} left · ${picks.length} ${picks.length === 1 ? "item" : "items"}` })),
    el("div", {},
      el("div", { class: "k", text: "You save" }),
      el("div", { class: `v${r.total_savings ? " green" : ""}`, text: money(r.total_savings, cur) })),
    el("div", {},
      el("div", { class: "k", text: "Deals found" }),
      el("div", { class: "v", text: eligible.length }),
      el("div", { class: "meter-caption", text: `${passed.length} passed over` })));
  requestAnimationFrame(() => requestAnimationFrame(() => { summary.querySelector(".meter i").style.width = `${pct}%`; }));

  const tabs = segmented("tab", [["picks", `Picks (${picks.length})`], ["all", `All (${eligible.length})`], ["passed", `Passed (${passed.length})`]], huntUi.tab, "wide");
  const sortSel = el("select", { "aria-label": "Sort by" },
    [["score", "Best deal"], ["discount", "Biggest discount"], ["savings", "Most saved"], ["price", "Lowest price"]]
      .map(([v, t]) => el("option", { value: v, text: t, selected: v === huntUi.sort })));
  const grid = el("div", { class: "grid" });

  const draw = () => {
    const list = { picks, all: eligible, passed }[huntUi.tab].slice().sort(SORTS[huntUi.sort]);
    grid.replaceChildren(...list.map((d) => dealCard(d, cur, hunt)));
    if (!list.length) {
      grid.replaceChildren(el("div", { class: "empty", style: "grid-column:1/-1;padding:40px" },
        el("p", { text: huntUi.tab === "passed" ? "Nothing was ruled out." : "Nothing fitted your budget and rules. Try a bigger budget or looser rules." })));
    }
  };
  tabs.addEventListener("change", (e) => { huntUi.tab = e.target.value; draw(); });
  sortSel.addEventListener("change", () => { huntUi.sort = sortSel.value; draw(); });
  draw();

  const exportBtn = el("button", { class: "icon-btn", "aria-label": "Export as Markdown", title: "Export as Markdown", onclick: () => exportMarkdown(hunt) }, icon("share"));
  const delBtn = el("button", { class: "icon-btn danger", "aria-label": "Delete hunt", title: "Delete", onclick: () => removeHunt(hunt) }, icon("trash"));

  const meta = [regionName, when(hunt.created_at),
    { natural: "Natural fibres", stretch: "Stretch OK", any: "Any fabric" }[hunt.request.fabric]].filter(Boolean).join(" · ");

  show(page(hunt.request.brief, { back: { href: "#/hunts", label: "Hunts" }, actions: [exportBtn, delBtn] },
    el("p", { class: "subtitle", text: meta }),
    hunt.sample && el("div", { class: "notice" },
      el("div", { class: "badge-icon", style: "background:var(--indigo)" }, icon("sparkles")),
      el("div", {}, el("h3", { text: "Sample hunt" }),
        el("p", { style: "margin:0", text: "Real products from an earlier run. Prices may have changed since. Run your own hunt for today’s deals." }))),
    summary,
    el("div", { class: "toolbar" }, tabs, el("label", { class: "sort" }, "Sort", sortSel)),
    grid));
}

async function exportMarkdown(hunt) {
  const res = await fetch(`/api/hunts/${hunt.id}/markdown`);
  const blob = new Blob([await res.text()], { type: "text/markdown" });
  const a = el("a", { href: URL.createObjectURL(blob), download: `deals-${hunt.id}.md` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ── Deal card & detail sheet ────────────────────────────────

function labelClass(label) {
  return { "Great deal": "great", "Good deal": "good" }[label] || "fair";
}

function imageWell(d) {
  const src = safeUrl(d.image_url);
  const ph = el("div", { class: "placeholder" }, icon("shirt"), el("span", { text: d.category }));
  if (!src) return [ph];
  const img = el("img", { src, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
  img.addEventListener("error", () => img.replaceWith(ph));
  return [img];
}

function favButton(d, currency) {
  const on = saved.has(d.url);
  const btn = el("button", { class: `fav${on ? " on" : ""}`, "aria-label": on ? "Remove from Saved" : "Save", "aria-pressed": String(on) },
    icon(on ? "heart-fill" : "heart"));
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const now = saved.toggle(d, currency);
    btn.classList.toggle("on", now);
    btn.setAttribute("aria-pressed", String(now));
    btn.setAttribute("aria-label", now ? "Remove from Saved" : "Save");
    btn.replaceChildren(icon(now ? "heart-fill" : "heart"));
    toast(now ? "Saved" : "Removed from Saved");
    if (!now && location.hash === "#/saved") savedView();
  });
  return btn;
}

function priceRow(d, currency) {
  return el("div", { class: "price-row" },
    el("span", { class: `price${d.discount_pct ? " sale" : ""}`, text: money(d.price, currency) }),
    d.discount_pct > 0 && d.original_price && el("span", { class: "was", text: money(d.original_price, currency) }));
}

function dealCard(d, currency, hunt) {
  const card = el("article", {
    class: `card${d.eligible === false ? " muted" : ""}`, tabindex: 0, role: "button",
    "aria-label": `${d.name}, ${d.retailer}, ${money(d.price, currency)}`,
  },
  el("div", { class: "well" }, imageWell(d),
    d.discount_pct > 0 && el("span", { class: "pill sale", text: `−${d.discount_pct}%` }),
    d.picked && huntUi.tab !== "picks" && el("span", { class: "pill picked" }, icon("check"), "Pick"),
    favButton(d, currency)),
  el("div", { class: "card-body" },
    el("div", { class: "retailer", text: d.retailer }),
    el("div", { class: "card-name", text: d.name }),
    priceRow(d, currency),
    el("div", { class: "card-meta" },
      d.eligible !== false && el("span", { class: `pill ${labelClass(d.label)}`, text: d.label }),
      d.verified && el("span", { class: "verified", title: "Price checked on the product page" }, icon("check-seal"))),
    d.rejection ? el("div", { class: "reason", text: d.rejection }) : d.justification && el("div", { class: "why", text: d.justification })));
  const open = () => openDeal(d, currency, hunt);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  return card;
}

let lastFocus = null;

function openDeal(d, currency) {
  const sheet = $("#sheet");
  const backdrop = $("#sheet-backdrop");
  lastFocus = document.activeElement;
  const ringColour = d.score >= 75 ? "var(--green)" : d.score >= 55 ? "var(--blue)" : "var(--label-3)";
  const fabricText = { natural: "Natural fibres", stretch: "Natural with stretch", synthetic: "Contains synthetics", unknown: "Not listed" }[d.fabric_verdict];
  const url = safeUrl(d.url);

  const ring = el("div", { class: "ring", style: `--p:0;--c:${ringColour}` });
  ring.innerHTML = '<svg viewBox="0 0 40 40"><circle class="track" cx="20" cy="20" r="15.9" pathLength="100"/><circle class="bar" cx="20" cy="20" r="15.9" pathLength="100"/></svg>';
  ring.append(el("b", { text: d.score }));
  requestAnimationFrame(() => requestAnimationFrame(() => ring.style.setProperty("--p", d.score)));

  const copyBtn = el("button", { class: "btn", "aria-label": "Copy link", title: "Copy link" }, icon("link"));
  copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(d.url); toast("Link copied"); } catch { toast("Couldn’t copy"); }
  });
  const fav = favButton(d, currency);
  fav.className = "btn";
  fav.style.position = "static";

  const facts = [
    ["Garment", d.garment],
    ["Category", d.category],
    ["Fabric", [d.composition, fabricText].filter(Boolean).join(" · ")],
    ["In stock", d.in_stock == null ? "Unknown" : d.in_stock ? "Yes" : "No"],
    ["Price checked", d.verified ? "Yes, on the product page" : "From search results only"],
    ["Style fit", `${d.style_fit} / 10`],
    d.savings > 0 && ["You save", money(d.savings, currency)],
  ].filter(Boolean);

  sheet.replaceChildren(
    el("div", { class: "grabber" }),
    el("div", { class: "sheet-head" }, el("button", { class: "close-btn", "aria-label": "Close", onclick: closeSheet }, icon("xmark"))),
    el("div", { class: "detail-hero" },
      el("div", { class: "well" }, imageWell(d), d.discount_pct > 0 && el("span", { class: "pill sale", text: `−${d.discount_pct}%` })),
      el("div", {},
        el("div", { class: "retailer", text: d.retailer }),
        el("h2", { id: "sheet-title", text: d.name }),
        el("div", { class: "detail-price" },
          el("span", { class: `price${d.discount_pct ? " sale" : ""}`, text: money(d.price, currency) }),
          d.discount_pct > 0 && d.original_price && el("span", { class: "was", text: money(d.original_price, currency) }),
          el("span", { class: `pill ${labelClass(d.label)}`, text: d.label })),
        el("div", { class: "score-line" }, ring,
          el("div", {}, el("div", { class: "k", text: "Deal score" }),
            el("div", { class: "v", text: d.rejection || (d.picked ? "In your picks" : "Worth a look") }))),
        el("div", { class: "detail-actions" },
          url ? el("a", { class: "btn primary", href: url, target: "_blank", rel: "noopener noreferrer" },
            `Buy at ${d.retailer}`, icon("arrow-up-right")) : el("span"),
          fav, copyBtn))),
    d.justification && el("div", { class: "quote" }, el("b", { text: "Why" }), d.justification),
    el("div", { class: "group-label", text: "Details" }),
    el("div", { class: "group" }, facts.map(([k, v]) =>
      el("div", { class: "row" }, el("span", { text: k }), el("span", { class: "value", text: v })))),
  );
  sheet.hidden = false;
  backdrop.hidden = false;
  sheet.classList.remove("closing");
  backdrop.classList.remove("closing");
  document.body.style.overflow = "hidden";
  sheet.scrollTop = 0;
  sheet.querySelector(".close-btn").focus();
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

// ── Saved ───────────────────────────────────────────────────

function savedView() {
  const items = Object.values(saved.all()).sort((a, b) => b.saved_at - a.saved_at);
  if (!items.length) {
    show(page("Saved", {}, el("div", { class: "empty" },
      icon("heart"), el("h2", { text: "Nothing saved yet" }),
      el("p", { text: "Tap the heart on any deal to keep it here." }))));
    return;
  }
  const total = items.reduce((s, d) => s + (d.currency === items[0].currency ? d.price : 0), 0);
  show(page("Saved", {},
    el("p", { class: "subtitle", text: `${items.length} ${items.length === 1 ? "item" : "items"} · ${money(total, items[0].currency)}` }),
    el("div", { class: "grid" }, items.map((d) => dealCard(d, d.currency)))));
}

// ── Router ──────────────────────────────────────────────────

async function route() {
  closeSheet();
  const [, name, id] = location.hash.split("/");
  if (name === "hunt" && id) return huntView(id);
  if (name === "hunts") return huntsView();
  if (name === "saved") return savedView();
  return newHuntView();
}

window.addEventListener("hashchange", route);

(async function start() {
  updateSavedCount();
  try {
    [config] = await Promise.all([api("/config"), refreshHunts()]);
  } catch (err) {
    view.replaceChildren(el("div", { class: "empty" }, el("h2", { text: "Can’t reach the server" }), el("p", { text: err.message })));
    return;
  }
  if (!location.hash) history.replaceState(null, "", hunts.length ? "#/hunts" : "#/new");
  route();
})();
