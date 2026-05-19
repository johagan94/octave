// Dashboard: integration health, sync card with progress bar, playlist sync dropdown.

import { api, ApiError } from "../api.js";
import { h, fmtMs, fmtAge, fmtDatetime } from "../h.js";
import { toast } from "../toast.js";

let pollTimer = 0;
let setupTimer = 0;
let mounted = false;
let playlistCache = [];

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

function buildSyncSelect(isRunning) {
  const sel = h("select", {
    id: "sync-select",
    disabled: isRunning,
    style: { flex: "1", minWidth: "0" },
  });
  sel.appendChild(h("option", { value: "" }, "All playlists"));
  for (const p of playlistCache) {
    const label = p.jellyfin_playlist_name || p.spotify_playlist_id;
    sel.appendChild(h("option", { value: p.spotify_playlist_id }, label));
  }
  return sel;
}

function syncCard(run) {
  const isRunning = run.status === "running";
  const pct = run.total > 0 ? Math.round((run.current / run.total) * 100) : 0;

  return h("div.card",
    h("div.card-row",
      h("div", h("h2", "Sync")),
      h("div", { style: { display: "flex", gap: "8px", alignItems: "center", flex: "1", justifyContent: "flex-end", maxWidth: "480px" } },
        buildSyncSelect(isRunning),
        h("button.primary", {
          id: "sync-btn",
          disabled: isRunning,
          onclick: () => triggerSync(),
        }, isRunning ? "Syncing…" : "Sync"),
      ),
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
      h("div.stat", h("span.num", String(run.waiting_lidarr || 0)),   h("span.label", "waiting lidarr")),
    ),

    run.schedule_cron && h("div", { style: { marginTop: "12px", display: "flex", gap: "16px", alignItems: "center" } },
      h("small", { style: { color: "var(--text-dim)" } },
        "⏰ schedule: ", h("code", run.schedule_cron)),
      run.next_run_at && h("small", { style: { color: "var(--text-dim)" } },
        "next run: ", h("strong", fmtDatetime(run.next_run_at))),
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
  const sel = document.getElementById("sync-select");
  const spotifyId = sel ? sel.value : "";
  const body = spotifyId ? { playlist_ids: [spotifyId] } : {};
  const label = sel && spotifyId
    ? (sel.options[sel.selectedIndex]?.text || spotifyId)
    : "all playlists";
  try {
    await api.post("/api/sync/all", body);
    toast(`Sync started: ${label}`);
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

function historyButton() {
  return h("div.card", { style: { marginTop: "12px" } },
    h("button", { onclick: () => loadHistory() }, "View sync history"),
    h("div", { id: "history-panel", style: { marginTop: "8px" } }),
  );
}

async function loadHistory() {
  const panel = document.getElementById("history-panel");
  if (!panel) return;
  try {
    const data = await api.get("/api/sync/history?limit=10");
    const runs = data.runs || [];
    if (!runs.length) {
      panel.innerHTML = "<small style='color:var(--text-dim)'>No sync history yet.</small>";
      return;
    }
    panel.innerHTML = "";
    for (const r of runs) {
      const statusCls = r.status === "success" ? "ok" : r.status === "error" ? "error" : "warn";
      panel.appendChild(h("div", { style: { padding: "4px 0", fontSize: "12px", borderBottom: "1px solid var(--border)" } },
        h("span.badge." + statusCls, { style: { marginRight: "8px" } }, r.status),
        h("span", { style: { color: "var(--text-dim)" } }, fmtAge(r.started_at)),
        r.matched > 0 && h("span", { style: { marginLeft: "8px", color: "var(--text)" } },
          r.matched + " matched, " + r.missing + " missing"),
        r.error && h("span", { style: { marginLeft: "8px", color: "var(--error)", fontSize: "11px" } },
          r.error.substring(0, 80)),
      ));
    }
  } catch (e) {
    panel.innerHTML = "<small style='color:var(--error)'>Failed to load history.</small>";
  }
}

let containerRef = null;
function render() {
  if (!mounted || !containerRef) return;
  containerRef.innerHTML = "";

  if (runCache) {
    containerRef.appendChild(syncCard(runCache));
    if (runCache.status !== "idle") {
      containerRef.appendChild(historyButton());
    }
  }

  if (setupCache) {
    const cards = [
      integrationCard("Spotify",  setupCache.spotify),
      integrationCard("Jellyfin", setupCache.jellyfin),
      integrationCard("Lidarr",   setupCache.lidarr),
    ];
    if (setupCache.listenbrainz) {
      cards.push(integrationCard("ListenBrainz", setupCache.listenbrainz));
    }
    if (setupCache.lastfm) {
      cards.push(integrationCard("Last.fm", setupCache.lastfm));
    }
    const grid = h("div.grid-3", ...cards);
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

    // Load playlists for the sync dropdown
    try {
      const data = await api.get("/api/playlists");
      playlistCache = (data.playlists || []).slice().sort((a, b) =>
        (a.jellyfin_playlist_name || "").localeCompare(b.jellyfin_playlist_name || "")
      );
    } catch (_) { /* non-fatal — dropdown will only show "All playlists" */ }

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
