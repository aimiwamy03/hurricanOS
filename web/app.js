// Shared helpers. No build step, no framework, no CDN.

// Store names, page titles and links come from the live web, so nothing is trusted:
// every value is escaped and every URL is checked before it reaches the DOM.
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function safeURL(url) {
  try {
    const parsed = new URL(String(url), window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function link(url, text) {
  const href = safeURL(url);
  if (!href) return esc(text);
  return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
}

async function getJSON(url, options = {}, timeoutMs = 12000) {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: stop.signal, cache: "no-store", ...options });
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    if (String(url).startsWith("/api/")) savedCopyNotice(response.headers.get("X-Shelfwatch-Saved"));
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

// ── Installed app (sw.js) ──────────────────────────────
// With no signal, the phone shows the last data it saved. Say so, with the time, so a
// saved "available online" is never read as live.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* works without it */ }));
}

function savedCopyNotice(savedIso) {
  let notice = document.getElementById("saved-copy");
  if (!savedIso) {
    if (notice) notice.remove();
    return;
  }
  if (!notice) {
    notice = document.createElement("div");
    notice.id = "saved-copy";
    notice.className = "banner saved-copy";
    notice.setAttribute("role", "status");
    const header = document.querySelector("header");
    if (header) header.after(notice);
    else document.body.prepend(notice);
  }
  const when = new Date(savedIso).toLocaleString("en-US", {
    timeZone: "Pacific/Honolulu", weekday: "short", hour: "numeric", minute: "2-digit",
  });
  notice.textContent = `No connection on this phone. Showing what it saved ${when} HST. Call 911 in an emergency.`;
}

function postJSON(url, body, timeoutMs = 120000) {
  return getJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeoutMs);
}

function ago(minutes) {
  if (minutes == null) return "time unknown";
  if (minutes < 1) return "checked just now";
  if (minutes < 60) return `checked ${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return `checked ${hours} h ${minutes % 60} min ago`;
}

// Every status comes from Walmart's online pickup search for the ZIP, not a shelf count,
// so the words say "online". "low" = only 1-2 matching products showed as available.
const STATUS_TEXT = {
  in_stock: "Available for pickup (online)",
  low: "Few online",
  out: "None online",
  unknown: "No matching pickup product",
};

function statusLabel(status) {
  const words = STATUS_TEXT;
  return words[status] || String(status || "unknown").replace("_", " ");
}

function pill(status) {
  return `<span class="pill ${esc(status || "unknown")}">${esc(statusLabel(status))}</span>`;
}

// "store" means a shelf at that store; "zip" means availability for that ZIP's pickup store.
function levelTag(level) {
  const words = { store: "store level", zip: "ZIP level", agent: "research" };
  return `<span class="tag">${esc(words[level] || level || "")}</span>`;
}

// Language-free safety pictograms (FLUX) with their fixed guidance sentence (code).
function tipsHtml(tips, heading) {
  if (!tips || !tips.length) return "";
  return `<div class="tips">${heading ? `<h3>${esc(heading)}</h3>` : ""}` + tips.map((tip) => `
    <div class="tip">${tip.image ? `<img src="${esc(tip.image)}" alt="">` : ""}<span>${esc(tip.text)}</span></div>`).join("") + `</div>`;
}

// Confidence is computed per check (app/confidence.py): source type, how many products
// backed the answer, agreement with the previous check, price sanity, then aged to now.
// The meter shows the level; "why" lists the exact reasons, so nothing is a black box.
function confidenceHtml(conf) {
  if (!conf) return "";
  const level = ["low", "medium", "high"].includes(conf.label) ? conf.label : "low";
  const filled = { low: 1, medium: 2, high: 3 }[level];
  const bars = [1, 2, 3].map((n) => `<i class="${n <= filled ? "on" : ""}"></i>`).join("");
  const pct = Math.round(Number(conf.score || 0) * 100);
  const reasons = (conf.reasons || []).map((reason) => `<li>${esc(reason)}</li>`).join("");
  return `<details class="conf ${level}">
    <summary title="How much to trust this check"><span class="meter" aria-hidden="true">${bars}</span><span class="label">${esc(level)} confidence &middot; ${esc(pct)}%</span><span class="why">why?</span></summary>
    <ul>${reasons}</ul>
  </details>`;
}
