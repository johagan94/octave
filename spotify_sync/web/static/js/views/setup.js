// Setup wizard: only really useful when integrations aren't configured,
// but also a useful "what does my server actually see?" reference page.

import { api } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let timer = 0;

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
  if (s === "done")    return "ok";
  if (s === "pending") return "warn";
  return "error";
}

function spotifyStep(intg) {
  if (!intg.configured) {
    return step("Spotify credentials", "todo",
      h("div",
        h("p", "Set ", h("code", "SPOTIFY_CLIENT_ID"), " and ", h("code", "SPOTIFY_CLIENT_SECRET"), " in your ", h("code", ".env"), "."),
        h("p", h("small", { style: { color: "var(--text-dim)" } },
          "Create a Spotify app at ",
          h("a", { href: "https://developer.spotify.com/dashboard", target: "_blank" }, "developer.spotify.com/dashboard"),
          ". Add ", h("code", "http://127.0.0.1:8888/callback"), " to its Redirect URIs."),
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
          "API key: Jellyfin → Dashboard → API Keys. User ID: visible in the URL when you click your user.")),
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

let containerRef = null;

async function refresh() {
  let status;
  try {
    status = await api.get("/api/setup/status");
  } catch (e) {
    containerRef.innerHTML = "";
    containerRef.appendChild(h("div.card", h("h2", "Setup error"), h("pre", e.message)));
    return;
  }

  containerRef.innerHTML = "";
  containerRef.appendChild(h("h2", "Setup"));
  containerRef.appendChild(h("p", { style: { color: "var(--text-dim)" } },
    "Configure each integration. Changes take effect immediately — re-pings happen every 30s on this page."));

  containerRef.appendChild(spotifyStep(status.spotify));
  containerRef.appendChild(jellyfinStep(status.jellyfin));
  containerRef.appendChild(lidarrStep(status.lidarr));

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
    h("button", { onclick: refresh }, "Run pings now"),
  ));
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div.empty", "Loading…"));
    await refresh();
    timer = setInterval(refresh, 30000);
  },
  unmount() {
    clearInterval(timer);
  },
};
