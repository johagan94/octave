// Logs view: initial tail via /api/logs, then live SSE stream.

import { api } from "../api.js";
import { getApiKey } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let evtSource = null;
let logEl = null;
let autoScroll = true;
let paused = false;

function classifyLine(line) {
  if (/\bERROR\b|\bCRITICAL\b/.test(line))    return "error";
  if (/\bWARN(ING)?\b/.test(line))            return "warn";
  if (/\bDEBUG\b/.test(line))                 return "debug";
  return "info";
}

function appendLine(text) {
  const div = document.createElement("div");
  div.className = "log-line " + classifyLine(text);
  div.textContent = text;
  logEl.appendChild(div);
  // Cap to last 5000 lines to avoid memory blow-up
  while (logEl.childNodes.length > 5000) logEl.removeChild(logEl.firstChild);
  if (autoScroll) logEl.scrollTop = logEl.scrollHeight;
}

function startStream() {
  // EventSource doesn't support custom headers, so the API key (if any)
  // travels as a query string. The auth dependency reads it from header
  // OR query param? Actually it only reads from header. So if API_KEY is
  // set, SSE won't auth. For now, log a warning; full fix is a server change.
  if (evtSource) evtSource.close();

  const url = "/api/logs/stream";
  if (getApiKey()) {
    toast("Live tail unavailable when API_KEY is set (EventSource can't send headers).", "warn", 6000);
    return;
  }

  evtSource = new EventSource(url);
  evtSource.addEventListener("log", (e) => {
    if (paused) return;
    appendLine(e.data);
  });
  evtSource.onerror = () => {
    // EventSource auto-reconnects; just let it.
  };
}

function stopStream() {
  if (evtSource) { evtSource.close(); evtSource = null; }
}

export default {
  async mount(container) {
    paused = false;
    autoScroll = true;

    container.appendChild(h("div",
      h("div.card-row", { style: { marginBottom: "12px" } },
        h("div", h("h2", { style: { margin: 0 } }, "Logs")),
        h("div", { style: { display: "flex", gap: "8px" } },
          h("button", { onclick: () => { paused = !paused; toast(paused ? "Paused" : "Resumed"); } }, "Pause/Resume"),
          h("button", {
            onclick: () => { autoScroll = !autoScroll; toast(autoScroll ? "Auto-scroll on" : "Auto-scroll off"); },
          }, "Auto-scroll"),
          h("button", { onclick: () => { logEl.innerHTML = ""; } }, "Clear"),
        ),
      ),
      h("div", { id: "log-area", class: "log-area" }),
    ));
    logEl = container.querySelector("#log-area");

    // Initial tail
    try {
      const data = await api.get("/api/logs?n=200");
      for (const line of data.lines || []) appendLine(line);
    } catch (e) {
      appendLine(`(error fetching logs: ${e.message})`);
    }

    startStream();
  },
  unmount() {
    stopStream();
  },
};
