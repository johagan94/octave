// Playlist CRUD: table + add form + sync_mode selector + bulk edit + delete.
// Also: export/import (JSON), smart playlist generator, similar-artist discovery.
// Respects SYNC_ALL_PLAYLISTS mode.

import { api, ApiError } from "../api.js";
import { h, fmtAge } from "../h.js";
import { toast } from "../toast.js";

const SPOTIFY_ID_RE = /(?:open\.spotify\.com\/playlist\/|spotify:playlist:)([a-zA-Z0-9]+)/;

function extractSpotifyId(input) {
  if (!input) return "";
  const m = input.match(SPOTIFY_ID_RE);
  return m ? m[1] : input.trim();
}

const SYNC_MODES = [
  { value: "add_only",  label: "add_only — never remove tracks" },
  { value: "full_sync", label: "full_sync — mirror Spotify exactly" },
  { value: "rebuild",   label: "rebuild — wipe and recreate every run" },
];

let containerRef = null;
let cache = [];
let syncStats = {};   // spotify_id → {matched, missing, status, started_at}
let syncAllActive = false;
// Set of spotify_playlist_ids currently checked
let selected = new Set();

// ── API helpers ───────────────────────────────────────────────────────

async function refresh() {
  try {
    const [playlistData, settingsData, historyData] = await Promise.all([
      api.get("/api/playlists"),
      api.get("/api/settings").catch(() => ({ settings: {} })),
      api.get("/api/sync/history?limit=1").catch(() => ({ runs: [] })),
    ]);

    cache = playlistData.playlists || [];
    selected = new Set([...selected].filter(id => cache.some(p => p.spotify_playlist_id === id)));

    // Check if SYNC_ALL_PLAYLISTS is on
    const syncAllSetting = settingsData?.settings?.SYNC_ALL_PLAYLISTS;
    syncAllActive = syncAllSetting?.value === "true";

    // Load per-playlist sync stats from the most recent run
    syncStats = {};
    const runs = historyData?.runs || [];
    if (runs.length > 0) {
      try {
        const detail = await api.get(`/api/sync/history/${runs[0].id}`);
        for (const item of detail?.items || []) {
          syncStats[item.spotify_id] = item;
        }
      } catch (_) { /* stats are cosmetic — ignore errors */ }
    }

    render();
  } catch (e) {
    toast(`Load failed: ${e.message}`, "error");
  }
}

async function onAdd(form) {
  const idRaw = form.elements.spotify.value.trim();
  if (!idRaw) { toast("Spotify ID or URL required", "error"); return; }
  const id = extractSpotifyId(idRaw);
  const name = form.elements.name.value.trim() || null;
  const mode = form.elements.mode.value;

  try {
    await api.post("/api/playlists", {
      spotify_playlist_id: id,
      jellyfin_playlist_name: name,
      sync_mode: mode,
    });
    toast(`Added: ${name || id}`);
    form.reset();
    refresh();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      toast(`Playlist ${id} already configured`, "warn");
    } else {
      toast(`Add failed: ${e.message}`, "error");
    }
  }
}

async function onDelete(id, name) {
  const existing = cache.find(p => p.spotify_playlist_id === id);
  if (existing && existing.configured === false) {
    toast("Auto-discovered playlist is not in the manual config", "warn");
    return;
  }
  if (!confirm(`Delete "${name || id}" from sync config?\n\n(This does NOT delete the playlist in Jellyfin.)`)) return;
  try {
    await api.del(`/api/playlists/${encodeURIComponent(id)}`);
    toast(`Removed: ${name || id}`);
    selected.delete(id);
    refresh();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}

async function onSyncOne(id, name) {
  try {
    await api.post("/api/sync/all", { playlist_ids: [id] });
    toast(`Sync started: ${name || id}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      toast("Sync already running", "warn");
    } else {
      toast(`Sync failed: ${e.message}`, "error");
    }
  }
}

function onExport(id, name) {
  // Trigger a file download through a temporary anchor element.
  const a = document.createElement("a");
  a.href = `/api/playlists/export/${encodeURIComponent(id)}`;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function onImport(file) {
  if (!file) return;
  let body;
  try {
    body = JSON.parse(await file.text());
  } catch (_) {
    toast("Not a valid JSON file", "error");
    return;
  }
  try {
    const res = await api.post("/api/playlists/import", body);
    toast(`Imported "${res.name}" — ${res.matched}/${res.total} tracks matched`
      + (res.skipped > 0 ? ` (${res.skipped} skipped)` : ""));
    refresh();
  } catch (e) {
    toast(`Import failed: ${e.message}`, "error");
  }
}

// ── Generate modal ────────────────────────────────────────────────────────────

const GEN_TYPES = [
  { value: "genre",      label: "By genre",          hint: "Random tracks matching a genre tag" },
  { value: "era",        label: "By era",             hint: "Random tracks from a year range" },
  { value: "unplayed",   label: "Unplayed tracks",    hint: "Tracks you've never listened to" },
  { value: "top_played", label: "Most played",        hint: "Your most-played tracks" },
  { value: "similar",    label: "Similar to a track", hint: "Tracks similar to a seed track (Jellyfin ID)" },
];

function openGenerateModal() {
  let typeEl, genreEl, fromYearEl, toYearEl, seedEl, nameEl, limitEl, paramsSection;

  function updateParams() {
    if (!paramsSection) return;
    paramsSection.innerHTML = "";
    const type = typeEl.value;
    if (type === "genre") {
      paramsSection.appendChild(h("div.field",
        h("label", "Genre"), genreEl = h("input", { type: "text", placeholder: "Rock, Jazz, Hip-Hop…", required: true })));
    } else if (type === "era") {
      paramsSection.appendChild(h("div.field",
        h("label", "From year"), fromYearEl = h("input", { type: "number", value: "1990", min: "1900", max: "2030" }),
        h("label", { style: { marginLeft: "8px" } }, "To year"), toYearEl = h("input", { type: "number", value: "1999", min: "1900", max: "2030" })));
    } else if (type === "similar") {
      paramsSection.appendChild(h("div.field",
        h("label", "Seed track Jellyfin ID"), seedEl = h("input", { type: "text", placeholder: "Paste a Jellyfin item ID", required: true })));
    }
  }

  const dialog = h("dialog",
    h("h3", { style: { margin: "0 0 16px" } }, "Generate playlist"),

    h("div.field",
      h("label", "Playlist name"), nameEl = h("input", { type: "text", required: true, placeholder: "My smart playlist" })),

    h("div.field",
      h("label", "Type"), typeEl = h("select", { onchange: updateParams },
        ...GEN_TYPES.map(t => h("option", { value: t.value }, `${t.label} — ${t.hint}`)))),

    paramsSection = h("div"),

    h("div.field",
      h("label", "Track count"), limitEl = h("input", { type: "number", value: "25", min: "1", max: "200" })),

    h("div", { style: { display: "flex", gap: "8px", marginTop: "16px" } },
      h("button.primary", {
        onclick: async () => {
          const name = nameEl.value.trim();
          const type = typeEl.value;
          const limit = parseInt(limitEl.value, 10) || 25;
          if (!name) { toast("Name is required", "error"); return; }
          const params = { limit };
          if (type === "genre") params.genre = genreEl?.value?.trim() || "";
          if (type === "era") { params.from_year = parseInt(fromYearEl?.value, 10); params.to_year = parseInt(toYearEl?.value, 10); }
          if (type === "similar") params.seed_track_id = seedEl?.value?.trim() || "";
          try {
            const res = await api.post("/api/playlists/generate", { name, type, params });
            toast(`Created "${res.name}" with ${res.track_count} tracks`);
            dialog.close();
            dialog.remove();
          } catch (e) {
            toast(`Generate failed: ${e.message}`, "error");
          }
        },
      }, "Generate"),
      h("button", { onclick: () => { dialog.close(); dialog.remove(); } }, "Cancel"),
    ),
  );

  document.body.appendChild(dialog);
  dialog.showModal();
  updateParams();
}

// ── Similar artist discovery ──────────────────────────────────────────────────

let discoverResults = null;   // null = not fetched, [] = fetched empty
let discoverLoading = false;

async function runDiscover() {
  if (discoverLoading) return;
  discoverLoading = true;
  renderDiscoverSection();
  try {
    const res = await api.get("/api/discover/similar_artists?limit=30");
    discoverResults = res.artists || [];
  } catch (e) {
    toast(`Discovery failed: ${e.message}`, "error");
    discoverResults = null;
  }
  discoverLoading = false;
  renderDiscoverSection();
}

function renderDiscoverSection() {
  const el = document.getElementById("discover-section");
  if (!el) return;
  el.innerHTML = "";

  if (discoverLoading) {
    el.appendChild(h("p", { style: { color: "var(--text-dim)" } }, "Querying Last.fm…"));
    return;
  }
  if (discoverResults === null) return;
  if (discoverResults.length === 0) {
    el.appendChild(h("p", { style: { color: "var(--text-dim)" } }, "No new similar artists found."));
    return;
  }

  el.appendChild(h("table",
    h("thead", h("tr",
      h("th", "Artist"), h("th", "Similar to"), h("th", "Score"), h("th", ""),
    )),
    h("tbody",
      ...discoverResults.map(a =>
        h("tr",
          h("td", a.name),
          h("td", { style: { color: "var(--text-dim)", fontSize: "12px" } },
            Array.isArray(a.similar_to) ? a.similar_to.join(", ") : a.similar_to),
          h("td", { style: { color: "var(--text-dim)", fontSize: "12px" } },
            Math.round((a.match || 0) * 100) + "%"),
          h("td",
            a.url
              ? h("a", { href: a.url, target: "_blank", rel: "noopener", style: { fontSize: "12px" } }, "Last.fm ↗")
              : null,
          ),
        )
      ),
    ),
  ));
}

async function onChangeMode(id, mode) {
  const existing = cache.find(p => p.spotify_playlist_id === id);
  if (!existing) return;
  try {
    if (existing.configured !== false) {
      await api.del(`/api/playlists/${encodeURIComponent(id)}`);
    }
    await api.post("/api/playlists", { ...existing, sync_mode: mode });
    toast(`Mode → ${mode}`);
    refresh();
  } catch (e) {
    toast(`Update failed: ${e.message}`, "error");
    refresh();
  }
}

// ── Bulk actions ──────────────────────────────────────────────────────

async function bulkChangeMode(mode) {
  if (selected.size === 0) { toast("No playlists selected", "warn"); return; }
  const targets = cache.filter(p => selected.has(p.spotify_playlist_id));
  let ok = 0, fail = 0;
  for (const p of targets) {
    if (p.sync_mode === mode) { ok++; continue; }
    try {
      if (p.configured !== false) {
        await api.del(`/api/playlists/${encodeURIComponent(p.spotify_playlist_id)}`);
      }
      await api.post("/api/playlists", { ...p, sync_mode: mode });
      ok++;
    } catch (_) { fail++; }
  }
  toast(fail === 0
    ? `${ok} playlist(s) → ${mode}`
    : `${ok} updated, ${fail} failed`, fail > 0 ? "warn" : "ok");
  refresh();
}

async function bulkDelete() {
  if (selected.size === 0) { toast("No playlists selected", "warn"); return; }
  const names = cache
    .filter(p => selected.has(p.spotify_playlist_id))
    .map(p => p.jellyfin_playlist_name || p.spotify_playlist_id);
  if (!confirm(`Remove ${selected.size} playlist(s) from sync config?\n\n${names.join("\n")}\n\n(This does NOT delete them in Jellyfin.)`)) return;
  let ok = 0, fail = 0;
  for (const id of [...selected]) {
    try {
      const item = cache.find(p => p.spotify_playlist_id === id);
      if (item && item.configured === false) {
        fail++;
        continue;
      }
      await api.del(`/api/playlists/${encodeURIComponent(id)}`);
      ok++;
    } catch (_) { fail++; }
  }
  selected.clear();
  toast(fail === 0 ? `${ok} removed` : `${ok} removed, ${fail} failed`, fail > 0 ? "warn" : "ok");
  refresh();
}

// ── Render ────────────────────────────────────────────────────────────

function toggleSelect(id, checked) {
  if (checked) selected.add(id);
  else selected.delete(id);
  updateBulkBar();
}

function toggleSelectAll(checked) {
  if (checked) cache.forEach(p => selected.add(p.spotify_playlist_id));
  else selected.clear();
  containerRef.querySelectorAll("input.row-check").forEach(cb => { cb.checked = checked; });
  updateBulkBar();
}

function updateBulkBar() {
  const bar = document.getElementById("bulk-bar");
  const countEl = document.getElementById("bulk-count");
  const allCb = document.getElementById("select-all-cb");
  if (!bar) return;
  const n = selected.size;
  if (n === 0) {
    bar.style.display = "none";
  } else {
    bar.style.display = "flex";
    if (countEl) countEl.textContent = `${n} selected`;
  }
  if (allCb) {
    allCb.indeterminate = n > 0 && n < cache.length;
    allCb.checked = n === cache.length && cache.length > 0;
  }
}

function statBadge(stats) {
  if (!stats) return h("span", { style: { color: "var(--text-dim)", fontSize: "11px" } }, "—");
  const ok = stats.matched || 0;
  const miss = stats.missing || 0;
  return h("span", { style: { fontSize: "11px", display: "flex", gap: "6px", alignItems: "center" } },
    h("span", { style: { color: "var(--ok)" } }, ok + " ✓"),
    miss > 0 && h("span", { style: { color: "var(--warn)" } }, miss + " ✗"),
    stats.started_at && h("span", { style: { color: "var(--text-dim)" } }, fmtAge(stats.started_at)),
  );
}

function row(p) {
  const id = p.spotify_playlist_id;
  const isChecked = selected.has(id);
  const stats = syncStats[id];
  const isConfigured = p.configured !== false;

  const modeSelect = h("select", {
    onchange: (e) => onChangeMode(id, e.target.value),
  });
  for (const m of SYNC_MODES) {
    modeSelect.appendChild(h("option", { value: m.value, selected: p.sync_mode === m.value }, m.label));
  }

  return h("tr",
    h("td", { style: { width: "1%", paddingRight: "4px" } },
      h("input", {
        type: "checkbox",
        class: "row-check",
        checked: isChecked,
        onchange: (e) => toggleSelect(id, e.target.checked),
      }),
    ),
    h("td", { style: { width: "42px" } },
      p.cover_url
        ? h("img.playlist-cover", { src: p.cover_url, loading: "lazy", title: p.jellyfin_playlist_name || id })
        : h("div.playlist-cover-placeholder"),
    ),
    h("td",
      h("div", { style: { display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" } },
        h("span", p.jellyfin_playlist_name || h("em", { style: { color: "var(--text-dim)" } }, "(unnamed)")),
        !isConfigured && h("span.badge", "auto"),
      ),
    ),
    h("td", h("span.id", id)),
    h("td", modeSelect),
    h("td", statBadge(stats)),
    h("td", { style: { width: "1%", whiteSpace: "nowrap" } },
      h("div", { style: { display: "flex", gap: "4px" } },
        h("button", { onclick: () => onSyncOne(id, p.jellyfin_playlist_name), title: "Sync this playlist now" }, "↺"),
        h("button", {
          onclick: () => onExport(id, p.jellyfin_playlist_name),
          title: "Export playlist as JSON backup",
          style: { fontSize: "12px" },
        }, "⬇"),
        h("button.danger", {
          onclick: () => onDelete(id, p.jellyfin_playlist_name),
          disabled: !isConfigured,
          title: isConfigured ? "Remove from sync config" : "Auto-discovered by sync-all",
        }, "✕"),
      ),
    ),
  );
}

function render() {
  containerRef.innerHTML = "";

  // ── SYNC_ALL banner ───────────────────────────────────────────────
  if (syncAllActive) {
    containerRef.appendChild(h("div.card", { style: { borderColor: "var(--accent)", background: "var(--accent-glow)" } },
      h("div.card-row",
        h("strong", { style: { color: "var(--accent)" } }, "⚡ Auto-sync active"),
        h("span.badge.ok", "SYNC_ALL_PLAYLISTS on"),
      ),
      h("p", { style: { color: "var(--text-dim)", marginTop: "8px", fontSize: "13px" } },
        "All playlists from your Spotify account are synced automatically. " +
        "The manual list below is ignored during sync — use it to configure sync modes per playlist " +
        "or to add playlists before enabling auto-sync."),
    ));
  }

  // ── Import card ───────────────────────────────────────────────────
  let importFileEl;
  const importCard = h("div.card",
    h("div.card-row",
      h("h2", { style: { margin: 0 } }, "Import playlist"),
      h("span", { style: { color: "var(--text-dim)", fontSize: "13px" } }, "Restore from an Octave JSON backup"),
    ),
    h("div.field", { style: { marginTop: "8px" } },
      importFileEl = h("input", { type: "file", accept: ".json,.octave.json" }),
    ),
    h("button.primary", {
      onclick: () => onImport(importFileEl.files[0]),
      style: { marginTop: "4px" },
    }, "Import"),
  );
  containerRef.appendChild(importCard);

  // ── Add form ──────────────────────────────────────────────────────
  const form = h("form.card",
    { onsubmit: (e) => { e.preventDefault(); onAdd(e.target); } },
    h("h2", "Add playlist"),
    h("div.field",
      h("label", { for: "spotify" }, "Spotify ID or URL"),
      h("input", { name: "spotify", id: "spotify", required: true,
        placeholder: "https://open.spotify.com/playlist/37i9... or just the ID" }),
    ),
    h("div.field",
      h("label", { for: "name" }, "Jellyfin playlist name (optional)"),
      h("input", { name: "name", id: "name",
        placeholder: "leave blank to auto-name from Spotify" }),
    ),
    h("div.field",
      h("label", { for: "mode" }, "Sync mode"),
      (() => {
        const s = h("select", { name: "mode", id: "mode" });
        for (const m of SYNC_MODES) s.appendChild(h("option", { value: m.value }, m.label));
        return s;
      })(),
    ),
    h("button.primary", { type: "submit" }, "Add"),
  );
  containerRef.appendChild(form);

  // ── Playlist table ────────────────────────────────────────────────
  const tableCard = h("div.card");

  const bulkModeSelect = h("select", { id: "bulk-mode-select", style: { width: "auto" } });
  for (const m of SYNC_MODES) bulkModeSelect.appendChild(h("option", { value: m.value }, m.value));

  const bulkBar = h("div", {
    id: "bulk-bar",
    style: { display: "none", alignItems: "center", gap: "8px", flexWrap: "wrap" },
  },
    h("span", { id: "bulk-count", style: { color: "var(--text-dim)", fontSize: "13px" } }, ""),
    h("span", { style: { color: "var(--border)" } }, "|"),
    h("span", { style: { fontSize: "13px", color: "var(--text-dim)" } }, "Set mode:"),
    bulkModeSelect,
    h("button", { onclick: () => bulkChangeMode(bulkModeSelect.value) }, "Apply"),
    h("span", { style: { color: "var(--border)" } }, "|"),
    h("button.danger", { onclick: bulkDelete }, "Remove selected"),
  );

  tableCard.appendChild(
    h("div.card-row", { style: { marginBottom: "12px" } },
      h("h2", { style: { margin: 0 } }, `Playlists (${cache.length})`),
      bulkBar,
    )
  );

  if (cache.length === 0) {
    tableCard.appendChild(h("div.empty", "No playlists configured yet."));
  } else {
    const allCb = h("input", {
      type: "checkbox",
      id: "select-all-cb",
      title: "Select all",
      onchange: (e) => toggleSelectAll(e.target.checked),
    });

    const tbl = h("table",
      h("thead", h("tr",
        h("th", { style: { width: "1%" } }, allCb),
        h("th", { style: { width: "42px" } }, ""),
        h("th", "Name"),
        h("th", "Spotify ID"),
        h("th", "Sync mode"),
        h("th", "Last sync"),
        h("th", ""),
      )),
      h("tbody", ...cache.map(row)),
    );
    tableCard.appendChild(tbl);
    updateBulkBar();
  }

  containerRef.appendChild(tableCard);

  // ── Generate card ─────────────────────────────────────────────────
  containerRef.appendChild(h("div.card",
    h("div.card-row",
      h("h2", { style: { margin: 0 } }, "Generate playlist"),
      h("span", { style: { color: "var(--text-dim)", fontSize: "13px" } },
        "Create a new Jellyfin playlist from a smart query"),
    ),
    h("div", { style: { marginTop: "8px" } },
      h("button.primary", { onclick: openGenerateModal }, "⚡ New smart playlist"),
    ),
  ));

  // ── Discover similar artists card ─────────────────────────────────
  containerRef.appendChild(h("div.card",
    h("div.card-row",
      h("h2", { style: { margin: 0 } }, "Discover similar artists"),
      h("span", { style: { color: "var(--text-dim)", fontSize: "13px" } },
        "Artists similar to your library that you don't have yet (requires Last.fm API key)"),
    ),
    h("div", { style: { marginTop: "8px" } },
      h("button", { onclick: runDiscover }, "🔍 Find similar artists"),
    ),
    h("div", { id: "discover-section", style: { marginTop: "12px" } }),
  ));
  renderDiscoverSection();
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div.empty", "Loading…"));
    await refresh();
  },
  unmount() {
    selected.clear();
  },
};
