// Minimal createElement helper. Saves verbosity in views without
// pulling in a full template/JSX system.
//
// Usage: h("div.card", {onclick: f}, h("h2", "Title"), "child text")

export function h(tag, ...rest) {
  const m = String(tag).match(/^([a-z][a-z0-9-]*)((?:[#.][a-z0-9_-]+)*)$/i);
  const t = m ? m[1] : tag;
  const el = document.createElement(t);
  if (m && m[2]) {
    for (const part of m[2].match(/[#.][a-z0-9_-]+/gi) || []) {
      if (part[0] === "#") el.id = part.slice(1);
      else el.classList.add(part.slice(1));
    }
  }

  let i = 0;
  if (rest[0] && typeof rest[0] === "object" && !(rest[0] instanceof Node) && !Array.isArray(rest[0])) {
    const props = rest[0]; i = 1;
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "html") el.innerHTML = v;
      else if (k in el) { try { el[k] = v; } catch { el.setAttribute(k, v); } }
      else el.setAttribute(k, v);
    }
  }
  for (; i < rest.length; i++) appendChild(el, rest[i]);
  return el;
}

function appendChild(el, child) {
  if (child == null || child === false) return;
  if (Array.isArray(child)) { child.forEach(c => appendChild(el, c)); return; }
  if (child instanceof Node) el.appendChild(child);
  else el.appendChild(document.createTextNode(String(child)));
}

export function fmtMs(ms) {
  if (ms == null) return "—";
  return ms < 1 ? "<1ms" : `${ms} ms`;
}

export function fmtAge(iso) {
  if (!iso) return "never";
  const ts = Date.parse(iso);
  if (isNaN(ts)) return "—";
  const sec = (Date.now() - ts) / 1000;
  if (sec < 60)   return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

/** Format an ISO datetime string as DD/MM/YYYY HH:MM (local time). */
export function fmtDatetime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  const dd   = String(d.getDate()).padStart(2, "0");
  const mm   = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  const hh   = String(d.getHours()).padStart(2, "0");
  const min  = String(d.getMinutes()).padStart(2, "0");
  return `${dd}/${mm}/${yyyy} ${hh}:${min}`;
}

const _DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Convert a simple cron expression to a human-readable string.
 *  Handles the most common homelab patterns; falls back to the raw string. */
export function fmtCron(expr) {
  if (!expr) return "";
  const p = expr.trim().split(/\s+/);
  if (p.length < 5) return expr;
  const [min, hour, dom, month, dow] = p;
  const hh  = hour.padStart(2, "0");
  const mm  = min.padStart(2, "0");
  const isEveryHour  = hour === "*";
  const isEveryMin   = min  === "*";
  const isDailyTime  = /^\d+$/.test(hour) && /^\d+$/.test(min);
  const isEveryDay   = dom === "*" && month === "*" && dow === "*";
  const isWeeklyDay  = dom === "*" && month === "*" && /^\d$/.test(dow);
  if (isEveryDay && isDailyTime)                           return `daily at ${hh}:${mm}`;
  if (isEveryDay && isEveryHour && isDailyTime)            return `every hour at :${mm}`;
  if (isEveryDay && isEveryHour && isEveryMin)             return "every minute";
  if (isWeeklyDay && isDailyTime)                          return `${_DAYS[+dow] ?? dow} at ${hh}:${mm}`;
  return expr; // fallback: show raw cron
}
