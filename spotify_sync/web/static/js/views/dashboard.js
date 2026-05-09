// Dashboard: integration health, sync card with progress bar, "Sync now".

import { api, ApiError } from "../api.js";
import { h, fmtMs, fmtAge } from "../h.js";
import { toast } from "../toast.js";

let pollTimer = 0;
let setupTimer = 0;
let mounted = false;

function badgeFor(intg) {
  if (!intg.configured) return h("span.badge.dim", "not configured");
  if (intg.reachable)   return h("span.badge.ok", "reachable");
  return h("span.badge.error", "unreachable");
}

function integrationCard(name, intg) {
  return h("div.card",
    h("div.card-row",
      h("strong", name),
      badgeFor(intg),
    ),
    h("div.health",
      intg.detail?.version && h("div.row",
        h("small", "version"),
        h("strong", intg.detail.version),
      ),
      intg.latency_ms != null && h("div.row",
        h("small", "latency"),
        h("strong", fmtMs(intg.latency_ms)),
      ),
      intg.error && h("div.row",
        h("small", "error"),
        h("strong", { style: { color: "var(--error)", fontFamily: "var(--mono)", fontSize: "11px" } }, intg.error),
      ),
    ),
  );
}

function syncCard(run) {
  const isRunning = run.status === "running";
  const pct = run.total > 0 ? Math.round((run.current / run.total) * 100) : 0;

  return h("div.card",
    h("div.card-row",
      h("div", h("h2", "Sync"), h("small", { style: { color: "var(--text-dim)" } }, "type: " + (run.type || "all"))),
      h("button.primary", {
        id: "sync-btn",
        disabled: isRunning,
        onclick: () => triggerSync(),
      }, isRunning ? "Syncing…" : "Sync now"),
    ),

    h("div.card-row", { style: { marginTop: "16px" } },
      h("span.badge." + statusKind(run.status), run.status),
      run.started_at && h("small", { style: { color: "var(--text-dim)" } },
        "started " + fmtAge(run.started_at)),
      run.finished_at && h("small", { style: { color: "var(--text-dim)" } },
        "finished " + fmtAge(run.finished_at)),
    ),

    isRunning && run.total > 0 && h("div",
      h("div.progress", h("div", { style: { width: pct + "%" } })),
      h("small", { style: { color: "var(--text-dim)" } },
        `${run.current} / ${run.total} playlists`),
    ),

    run.error && h("div", { style: { marginTop: "12px" } },
      h("small", { style: { color: "var(--text-dim)" } }, "error"),
      h("pre", { style: { marginTop: "4px" } }, run.error),
    ),

    h("div.stats", { style: { marginTop: "16px" } },
      h("div.stat", h("span.num", String(run.matched || 0)),          h("span.label", "matched")),
      h("div.stat", h("span.num", String(run.missing || 0)),          h("span.label", "missing")),
      h("div.stat", h("span.num", String(run.albums_requested || 0)), h("span.label", "albums requested")),
    ),
  );
}

function statusKind(status) {
  if (status === "running") return "warn";
  if (status === "success") return "ok";
  if (status === "error")   return "error";
  return "dim";
}

async function triggerSync() {
  try {
    await api.post("/api/sync/all");
    toast("Sync started");
    refreshSync();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      toast("Sync already running", "warn");
    } else {
      toast(`Sync failed: ${e.message}`, "error");
    }
  }
}

let setupCache = null;
let runCache = null;

async function refreshSetup() {
  try {
    setupCache = await api.get("/api/setup/status");
    render();
  } catch (e) {
    if (e.status === 401) {
      toast("API key required", "error");
    }
  }
}

async function refreshSync() {
  try {
    runCache = await api.get("/api/sync/status");
    render();
  } catch (e) { /* ignore — next tick will retry */ }
}

let containerRef = null;
function render() {
  if (!mounted || !containerRef) return;
  containerRef.innerHTML = "";

  if (runCache) {
    containerRef.appendChild(syncCard(runCache));
  }

  if (setupCache) {
    const grid = h("div.grid-3",
      integrationCard("Spotify",  setupCache.spotify),
      integrationCard("Jellyfin", setupCache.jellyfin),
      integrationCard("Lidarr",   setupCache.lidarr),
    );
    containerRef.appendChild(grid);

    const stats = h("div.card",
      h("div.card-row",
        h("strong", "Configuration"),
        h("span.badge." + (setupCache.config_loaded ? "ok" : "warn"),
          setupCache.config_loaded ? "loaded" : "missing"),
      ),
      h("div.stats", { style: { marginTop: "12px" } },
        h("div.stat", h("span.num", String(setupCache.playlist_count)), h("span.label", "playlists configured")),
      ),
    );
    containerRef.appendChild(stats);
  }
}

export default {
  async mount(container) {
    mounted = true;
    containerRef = container;
    container.appendChild(h("div", { id: "dashboard-content" },
      h("div.empty", "Loading…"),
    ));
    containerRef = container.querySelector("#dashboard-content");

    await Promise.all([refreshSetup(), refreshSync()]);
    render();

    // Poll sync status — fast while running, slow when idle
    const tick = async () => {
      if (!mounted) return;
      await refreshSync();
      const interval = runCache?.status === "running" ? 1500 : 5000;
      pollTimer = setTimeout(tick, interval);
    };
    pollTimer = setTimeout(tick, 1500);

    // Re-check setup every 30s
    setupTimer = setInterval(refreshSetup, 30000);
  },
  unmount() {
    mounted = false;
    clearTimeout(pollTimer);
    clearInterval(setupTimer);
  },
};
