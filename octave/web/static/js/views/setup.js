// Setup wizard: only really useful when integrations aren't configured,
// but also a useful "what does my server actually see?" reference page.

import { api } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let timer = 0;
let lastKey = "";  // diff key — only rebuild DOM when data changes

function step(title, status, body) {
  return h("div.setup-step" + (status === "done" ? ".done" : ""),
    h("div.card-row",
      h("h3", { style: { margin: 0 } }, title),
      h("span.badge." + statusKind(status), status),
    ),
    h("div", body),
  );
}

function statusKind(s) {
  if (s === "done" || s === "reachable") return "ok";
  if (s === "pending" || s === "running" || s === "partial") return "warn";
  if (s === "error" || s === "unreachable") return "error";
  return "dim";
}

function discoveryStep(name, intg, description) {
  const isOptional = !intg.configured;
  const status = isOptional ? "not configured" : intg.reachable ? "reachable" : "unreachable";
  return h("div.card",
    h("div.card-row",
      h("strong", name),
      h("span.badge." + (isOptional ? "dim" : intg.reachable ? "ok" : "warn"), status),
    ),
    h("p", { style: { color: "var(--text-dim)", fontSize: "12px", marginTop: "4px" } }, description),
    intg.latency_ms != null && h("small", { style: { color: "var(--text-dim)" } }, "Latency: " + intg.latency_ms + "ms"),
    intg.error && !isOptional && h("pre", { style: { marginTop: "4px", fontSize: "11px" } }, intg.error),
  );
}

function spotifyStep(intg) {
  if (!intg.configured) {
    return step("Connect Spotify", "todo",
      h("div",
        h("p", "Go to ", h("strong", "Settings -> Spotify"), " and click ", h("strong", "Connect Spotify"), ". No developer account needed — it uses the bundled app with PKCE OAuth."),
        h("p", h("small", { style: { color: "var(--text-dim)" } },
          "Advanced: to use your own Spotify app, create one at ",
          h("a", { href: "https://developer.spotify.com/dashboard", target: "_blank" }, "developer.spotify.com/dashboard"),
          ", add ", h("code", "http://127.0.0.1:8888/callback"), " to its Redirect URIs, and set your Client ID in Settings."),
        ),
      ),
    );
  }

  // Client credentials mode — public playlists only, no user token
  if (intg.detail && intg.detail.mode === "client_credentials") {
    return step("Spotify OAuth", "partial",
      h("div",
        h("p", "Client credentials active — ", h("strong", "public playlists only"), ". ",
          "Go to ", h("strong", "Settings -> Spotify"), " and click ", h("strong", "Connect Spotify"), " to authorize with your account for full access."),
        h("p", h("small", { style: { color: "var(--text-dim)" } },
          "PKCE OAuth grants access to private playlists, saved tracks, and library. ",
          "No developer account needed — uses the bundled Spotify app."),
        ),
      ),
    );
  }

  if (!intg.reachable) {
    return step("Spotify OAuth", "pending",
      h("div",
        h("p", "Credentials are set, but no token cache exists. ",
          "Run a one-shot sync from the Dashboard's ", h("strong", "Sync now"), " button. ",
          "Your browser will open the Spotify auth page automatically (or check the logs for the URL)."),
      ),
    );
  }
  return step("Spotify", "done",
    h("p", "Authenticated. Token will refresh automatically."),
  );
}

function jellyfinStep(intg) {
  if (!intg.configured) {
    return step("Jellyfin", "todo",
      h("div",
        h("p", "Set ", h("code", "JELLYFIN_URL"), ", ", h("code", "JELLYFIN_API_KEY"), " and ", h("code", "JELLYFIN_USER_ID"), " in ", h("code", ".env"), "."),
        h("p", h("small", { style: { color: "var(--text-dim)" } },
          "API key: Jellyfin -> Dashboard -> API Keys. User ID: visible in the URL when you click your user.")),
      ),
    );
  }
  if (!intg.reachable) {
    return step("Jellyfin", "pending",
      h("div",
        h("p", "Cannot reach the server."),
        h("pre", intg.error || "(no detail)"),
        h("p", h("small", { style: { color: "var(--text-dim)" } },
          "If the container can't see your Jellyfin host, double-check that ", h("code", "JELLYFIN_URL"),
          " resolves from inside the container (use the LAN IP, not ", h("code", "localhost"), ").")),
      ),
    );
  }
  return step("Jellyfin", "done",
    h("p", "Reachable",
      intg.detail?.version && h("span", { style: { color: "var(--text-dim)" } }, `, version ${intg.detail.version}`)),
  );
}

function lidarrStep(intg) {
  if (!intg.configured) {
    return step("Lidarr", "todo",
      h("p", "Set ", h("code", "LIDARR_URL"), " and ", h("code", "LIDARR_API_KEY"), " in ", h("code", ".env"), ". (Optional — disable Lidarr by leaving these unset, but missing albums won't be auto-requested.)"),
    );
  }
  if (!intg.reachable) {
    return step("Lidarr", "pending", h("pre", intg.error || "(unreachable)"));
  }
  return step("Lidarr", "done",
    h("p", "Reachable",
      intg.detail?.version && h("span", { style: { color: "var(--text-dim)" } }, `, version ${intg.detail.version}`)),
  );
}

function buildDOM(status, containerRef) {
  containerRef.innerHTML = "";
  containerRef.appendChild(h("h2", "Health"));
  containerRef.appendChild(h("p", { style: { color: "var(--text-dim)" } },
    "Integration health checks. Pings run every 60s — or click Re-test now."));

  containerRef.appendChild(spotifyStep(status.spotify));
  containerRef.appendChild(jellyfinStep(status.jellyfin));
  containerRef.appendChild(lidarrStep(status.lidarr));
  if (status.listenbrainz) {
    containerRef.appendChild(discoveryStep("ListenBrainz", status.listenbrainz,
      "MusicBrainz ID resolution, popularity data, and collaborative filtering recommendations. Free and open-source. Set LISTENBRAINZ_TOKEN (optional)."));
  }
  if (status.lastfm) {
    containerRef.appendChild(discoveryStep("Last.fm", status.lastfm,
      "Playcounts, similar artist/track discovery, and scrobble history. Free API key required. Set LASTFM_API_KEY (optional)."));
  }

  containerRef.appendChild(h("div.card", { style: { marginTop: "16px" } },
    h("div.card-row",
      h("strong", "Configuration"),
      h("span.badge." + (status.config_loaded ? "ok" : "warn"), status.config_loaded ? "loaded" : "missing"),
    ),
    h("p", { style: { color: "var(--text-dim)", marginTop: "8px" } },
      `${status.playlist_count} playlist(s) configured.`),
    h("button.primary", { onclick: () => location.hash = "#/playlists" }, "Manage playlists"),
  ));

  containerRef.appendChild(h("div.card",
    h("h3", "Re-test"),
    h("button", { onclick: () => { lastKey = ""; refresh(); } }, "Run pings now"),
  ));
}

let containerRef = null;

async function refresh() {
  let status;
  try {
    status = await api.get("/api/setup/status");
  } catch (e) {
    // On error only show a card if we have nothing yet
    if (!containerRef.querySelector("h2")) {
      containerRef.innerHTML = "";
      containerRef.appendChild(h("div.card", h("h2", "Setup error"), h("pre", e.message)));
    }
    return;
  }

  // Only rebuild the DOM if data actually changed — prevents flash every 60s
  const key = JSON.stringify(status);
  if (key === lastKey) return;
  lastKey = key;
  buildDOM(status, containerRef);
}

export default {
  async mount(container) {
    containerRef = container;
    lastKey = "";
    container.appendChild(h("div.empty", "Loading\u2026"));
    await refresh();
    timer = setInterval(refresh, 60000);
  },
  unmount() {
    clearInterval(timer);
    lastKey = "";
  },
};
