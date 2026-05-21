// Missing tracks: browse playlists with unmatched tracks, download CSV.
import { api } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let containerRef = null;
let cache = {};

async function refresh() {
  try {
    const data = await api.get("/api/sync/missing");
    cache = data.playlists || {};
    render();
  } catch (e) {
    toast("Load failed: " + e.message, "error");
  }
}

function downloadCsv(spotifyId, name) {
  window.open("/api/sync/missing/download/" + encodeURIComponent(spotifyId), "_blank");
}

function playlistCard(id, pl) {
  const tracks = pl.tracks || [];
  const albums = new Set(tracks.map(function(t) { return t.album; })).size;

  return h("div.card",
    h("div.card-row",
      h("strong", pl.playlist_name || id),
      h("span.badge.warn", tracks.length + " missing"),
    ),
    h("div", { style: { marginTop: "8px", fontSize: "12px", color: "var(--text-dim)" } },
      "Across " + albums + " albums — ",
      h("button", {
        onclick: function() { downloadCsv(id, pl.playlist_name); },
        style: { fontSize: "12px" },
      }, "Download CSV"),
    ),
    h("div", { style: { marginTop: "8px", maxHeight: "200px", overflowY: "auto" } },
      h("table", { style: { fontSize: "11px" } },
        h("thead", h("tr",
          h("th", "Track"),
          h("th", "Artist"),
          h("th", "Album"),
        )),
        h("tbody", ...tracks.slice(0, 20).map(function(t) {
          return h("tr",
            h("td",
              t.spotify_url
                ? h("a", { href: t.spotify_url, target: "_blank", style: { color: "var(--accent)" } }, t.title)
                : t.title
            ),
            h("td", t.artist),
            h("td", { style: { color: "var(--text-dim)" } }, t.album),
          );
        })),
        tracks.length > 20 && h("tr",
          h("td", { colspan: 3, style: { color: "var(--text-dim)", fontStyle: "italic" } },
            "+ " + (tracks.length - 20) + " more tracks..."),
        ),
      ),
    ),
  );
}

function render() {
  if (!containerRef) return;
  containerRef.innerHTML = "";

  containerRef.appendChild(h("h2", "Missing Tracks"));
  containerRef.appendChild(h("p", { style: { color: "var(--text-dim)" } },
    "Tracks found in Spotify but not matched in Jellyfin. Use Lidarr to request missing albums."));

  var ids = Object.keys(cache);
  if (!ids.length) {
    containerRef.appendChild(h("div.empty",
      h("strong", "No missing tracks"),
      h("span", "Run a sync to populate this view, or enjoy the rare quiet moment if everything is already matched."),
    ));
    return;
  }

  for (var i = 0; i < ids.length; i++) {
    containerRef.appendChild(playlistCard(ids[i], cache[ids[i]]));
  }
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div.empty",
      h("strong", "Loading missing tracks"),
      h("span", "Checking the latest sync results..."),
    ));
    await refresh();
  },
  unmount() {},
};
