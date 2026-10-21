// Shared building blocks: the element builder, formatting, the API client,
// and the controls and sheet every view uses.

export const $ = (sel, root = document) => root.querySelector(sel);
const CUR = "AUD";

// ── Helpers ─────────────────────────────────────────────────

export function el(tag, attrs = {}, ...kids) {
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

export function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `i ${cls}`);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

export function money(value) {
  if (value == null) return "";
  return new Intl.NumberFormat("en-AU", {
    style: "currency", currency: CUR, minimumFractionDigits: value % 1 ? 2 : 0, maximumFractionDigits: 2,
  }).format(value);
}

export function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso)) / 6e4);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
}

// Shopify's image CDN resizes on request; ask for card-sized images.
export function sized(src, width) {
  const url = safeUrl(src);
  if (!url) return null;
  if (/cdn\.shopify\.com|\/cdn\/shop\//.test(url)) return `${url}${url.includes("?") ? "&" : "?"}width=${width}`;
  return url;
}

export async function api(path, opts = {}) {
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

export const local = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
};

let toastTimer;
export function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

export function segmented(name, options, value, onchange, cls = "") {
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

export function toggle(checked, onchange, label) {
  const input = el("input", { type: "checkbox", role: "switch", "aria-label": label, checked });
  input.addEventListener("change", () => onchange(input.checked));
  return el("label", { class: "switch" }, input, el("span"));
}

export function emptyState(iconName, title, text, ...actions) {
  return el("div", { class: "empty" }, icon(iconName), el("h2", { text: title }), el("p", { text }), ...actions);
}

// ── Sheet ───────────────────────────────────────────────────

let lastFocus = null;

export function openSheet(title, content) {
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

export function closeSheet() {
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
