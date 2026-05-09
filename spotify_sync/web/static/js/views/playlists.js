// Playlist CRUD: table + add form + sync_mode selector + delete button.

import { api, ApiError } from "../api.js";
import { h } from "../h.js";
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

async function refresh() {
  try {
    const data = await api.get("/api/playlists");
    cache = data.playlists || [];
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
  if (!confirm(`Delete "${name || id}" from sync config?\n\n(This does NOT delete the playlist in Jellyfin.)`)) return;
  try {
    await api.del(`/api/playlists/${encodeURIComponent(id)}`);
    toast(`Removed: ${name || id}`);
    refresh();
  } catch (e) {
    toast(`Delete failed: ${e.message}`, "error");
  }
}

async function onChangeMode(id, mode) {
  // The current API only supports add+delete; to update mode, delete then re-add.
  // Find the existing entry to preserve fields.
  const existing = cache.find(p => p.spotify_playlist_id === id);
  if (!existing) return;
  try {
    await api.del(`/api/playlists/${encodeURIComponent(id)}`);
    await api.post("/api/playlists", { ...existing, sync_mode: mode });
    toast(`Mode → ${mode}`);
    refresh();
  } catch (e) {
    toast(`Update failed: ${e.message}`, "error");
    refresh();
  }
}

function row(p) {
  const id = p.spotify_playlist_id;
  const select = h("select", {
    onchange: (e) => onChangeMode(id, e.target.value),
  });
  for (const m of SYNC_MODES) {
    const opt = h("option", { value: m.value, selected: p.sync_mode === m.value }, m.label);
    select.appendChild(opt);
  }
  return h("tr",
    h("td", p.jellyfin_playlist_name || h("em", { style: { color: "var(--text-dim)" } }, "(unnamed)")),
    h("td", h("span.id", id)),
    h("td", select),
    h("td", { style: { width: "1%", whiteSpace: "nowrap" } },
      h("button.danger", { onclick: () => onDelete(id, p.jellyfin_playlist_name) }, "Remove"),
    ),
  );
}

function render() {
  containerRef.innerHTML = "";

  // Add form
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

  // Existing playlists table
  const tableCard = h("div.card", h("h2", `Configured (${cache.length})`));
  if (cache.length === 0) {
    tableCard.appendChild(h("div.empty", "No playlists configured yet."));
  } else {
    const tbl = h("table",
      h("thead", h("tr",
        h("th", "Name"),
        h("th", "Spotify ID"),
        h("th", "Sync mode"),
        h("th", ""),
      )),
      h("tbody", ...cache.map(row)),
    );
    tableCard.appendChild(tbl);
  }
  containerRef.appendChild(tableCard);
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div.empty", "Loading…"));
    await refresh();
  },
  unmount() {},
};
