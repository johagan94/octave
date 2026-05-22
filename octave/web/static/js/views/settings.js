// Settings view: form-based credential and runtime-knob editor.
// Secrets are masked by default with a show/hide toggle.

import { api } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let containerRef = null;
let fields = {};
let originalValues = {};

const SECTIONS = [
  {
    title: "Spotify",
    statusId: "spotify-status",
    description: "Click 'Connect Spotify' and log in — no developer account needed. Uses PKCE OAuth (no client secret).",
    help: "Leave Client ID blank to use the bundled app. With your own Spotify app, add the redirect URI shown after Connect Spotify (usually this Octave host on port 8888) in developer.spotify.com/dashboard. Client Secret is only for legacy (non-PKCE) flows.",
    fields: [
      { key: "SPOTIFY_CLIENT_ID", label: "Client ID (optional)", type: "text", required: false },
      { key: "SPOTIFY_CLIENT_SECRET", label: "Client Secret (optional)", type: "password", required: false },
      { key: "SPOTIFY_REDIRECT_URI", label: "Redirect URI", type: "text", required: false, placeholder: "http://127.0.0.1:8888/callback" },
    ],
    actions: [
      { label: "Connect Spotify", action: "connectSpotify" },
      { label: "Check Status", action: "checkSpotifyStatus" },
      { label: "Disconnect", action: "disconnectSpotify" },
    ],
  },
  {
    title: "Jellyfin",
    description: "Media server URL and API credentials.",
    help: "API key: Jellyfin Dashboard -> API Keys. User ID: visible in URL when you click your user.",
    fields: [
      { key: "JELLYFIN_URL", label: "Server URL", type: "url", required: true },
      { key: "JELLYFIN_API_KEY", label: "API Key", type: "password", required: true },
      { key: "JELLYFIN_USER_ID", label: "User ID", type: "text", required: true },
    ],
  },
  {
    title: "Lidarr",
    description: "Music management for auto-requesting missing albums.",
    help: "Optional -- leave unset to disable Lidarr integration.",
    fields: [
      { key: "LIDARR_URL", label: "Server URL", type: "url", required: false },
      { key: "LIDARR_API_KEY", label: "API Key", type: "password", required: false },
    ],
  },
  {
    title: "ListenBrainz",
    description: "MusicBrainz ID resolution and collaborative filtering.",
    help: "Optional. Get your token from listenbrainz.org/profile/",
    fields: [
      { key: "LISTENBRAINZ_TOKEN", label: "Token", type: "password", required: false },
    ],
  },
  {
    title: "Last.fm",
    description: "Playcounts, similar artist/track discovery.",
    help: "Optional. Get your API key from last.fm/api/account/create",
    fields: [
      { key: "LASTFM_API_KEY", label: "API Key", type: "password", required: false },
      { key: "LASTFM_USERNAME", label: "Username", type: "text", required: false },
    ],
  },
  {
    title: "Security",
    description: "HTTP Basic Auth for the web interface.",
    help: "Set a password to protect Octave with HTTP Basic Auth. The browser handles the login prompt natively — no page reload or extra steps needed. Leave Password blank for open access (LAN-trust mode). Username defaults to 'octave' if left blank.",
    fields: [
      { key: "AUTH_USERNAME", label: "Username", type: "text", required: false, placeholder: "octave" },
      { key: "AUTH_PASSWORD", label: "Password", type: "password", required: false },
    ],
  },
  {
    title: "Runtime",
    description: "Server behavior and scheduling.",
    help: "Sync All My Spotify Playlists: when on, syncs every playlist in your Spotify account (owned + followed) and ignores the manual Playlists list; Spotify-owned editorial playlists are skipped automatically. Changes to SYNC_SCHEDULE take effect immediately. Other changes may require a container restart.",
    fields: [
      { key: "SYNC_SCHEDULE", label: "Cron Schedule", type: "text", required: false, placeholder: "0 2 * * *" },
      { key: "SYNC_ALL_PLAYLISTS", label: "Sync All My Spotify Playlists", type: "checkbox", required: false },
      { key: "SYNC_ON_STARTUP", label: "Sync on Startup", type: "checkbox", required: false },
      { key: "LOG_LEVEL", label: "Log Level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"], required: false },
      { key: "TZ", label: "Timezone", type: "text", required: false, placeholder: "UTC" },
    ],
  },
];

function fieldInput(field, value, source) {
  const isSecret = field.type === "password";
  const isCheckbox = field.type === "checkbox";
  const isSelect = field.type === "select";
  const isSet = !!value;
  const sourceBadge = source === "env"
    ? h("span.badge.warn", "env")
    : source === "file"
      ? h("span.badge.ok", "saved")
      : h("span.badge.dim", "unset");

  const children = [
    h("label", { for: field.key }, field.label),
    sourceBadge,
  ];

  if (isCheckbox) {
    children.push(
      h("input", {
        id: field.key,
        type: "checkbox",
        checked: value === "true",
        "data-key": field.key,
      })
    );
  } else if (isSelect) {
    const opts = field.options.map(opt =>
      h("option", { value: opt, selected: value === opt }, opt)
    );
    children.push(h("select", { id: field.key, "data-key": field.key }, ...opts));
  } else {
    children.push(
      h("input", {
        id: field.key,
        type: isSecret ? "password" : field.type === "url" ? "url" : "text",
        value: value || "",
        placeholder: field.placeholder || "",
        autocomplete: "off",
        "data-key": field.key,
        "data-secret": isSecret ? "1" : "",
      })
    );
    if (isSecret && value) {
      children.push(
        h("button.show-toggle", {
          onclick: (e) => {
            var inp = e.target.parentElement.querySelector('input[data-key="' + field.key + '"]');
            if (inp.type === "password") {
              inp.type = "text";
              e.target.textContent = "hide";
            } else {
              inp.type = "password";
              e.target.textContent = "show";
            }
          },
        }, "show")
      );
    }
  }

  if (field.required && !isSet) {
    children.push(h("span.badge.error", "required"));
  }

  return h("div.setting-field", ...children);
}

function sectionHTML(section, settings) {
  var fieldEls = section.fields.map(function(f) {
    var s = settings[f.key] || {};
    return fieldInput(f, s.value, s.source);
  });

  var actionEls = (section.actions || []).map(function(a) {
    return h("button", { onclick: function() { handleAction(a.action); } }, a.label);
  });

  return h("div.card",
    h("div.card-row",
      h("h3", section.title),
    ),
    h("p", { style: { color: "var(--text-dim)", fontSize: "13px" } }, section.description),
    section.statusId
      ? h("p", { id: section.statusId, style: { fontSize: "13px", margin: "8px 0", fontWeight: "600" } }, "Status: checking…")
      : null,
    ...fieldEls,
    actionEls.length > 0 ? h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } }, ...actionEls) : null,
    h("p", { style: { color: "var(--text-dim)", fontSize: "11px", marginTop: "8px" } }, section.help),
  );
}

function setSpotifyStatusText(text, color) {
  var el = document.getElementById("spotify-status");
  if (!el) return;
  el.textContent = text;
  el.style.color = color || "var(--text-dim)";
}

async function refreshSpotifyStatus() {
  try {
    var res = await api.get("/api/spotify/auth-status");
    if (res.authenticated) {
      var exp = res.expires_at ? new Date(res.expires_at * 1000).toLocaleString() : "unknown";
      setSpotifyStatusText("Status: Connected ✅  (token valid until " + exp + ")", "var(--ok)");
    } else if (!res.client_id_available) {
      setSpotifyStatusText("Status: No Client ID available — set one below or ship a bundled default", "var(--error, #c00)");
    } else {
      var note = res.bundled_client_id ? " (using bundled app — no dev account needed)" : "";
      setSpotifyStatusText("Status: Not connected" + note + " — click 'Connect Spotify'", "var(--warn, #b80)");
    }
    return res.authenticated === true;
  } catch (e) {
    setSpotifyStatusText("Status: unknown (" + e.message + ")", "var(--error, #c00)");
    return false;
  }
}

let _spotifyPollTimer = null;

function startSpotifyPolling() {
  if (_spotifyPollTimer) clearInterval(_spotifyPollTimer);
  var deadline = Date.now() + 120000; // poll up to 2 min
  _spotifyPollTimer = setInterval(async function () {
    var done = await refreshSpotifyStatus();
    if (done || Date.now() > deadline) {
      clearInterval(_spotifyPollTimer);
      _spotifyPollTimer = null;
      if (done) toast("Spotify connected ✅");
    }
  }, 3000);
}

async function handleAction(action) {
  if (action === "connectSpotify") {
    // Save Client ID first so the server can use it
    var clientIdEl = document.getElementById("SPOTIFY_CLIENT_ID");
    var redirectUriEl = document.getElementById("SPOTIFY_REDIRECT_URI");
    var updates = {};
    if (clientIdEl && clientIdEl.value.trim()) updates.SPOTIFY_CLIENT_ID = clientIdEl.value.trim();
    if (redirectUriEl && redirectUriEl.value.trim()) updates.SPOTIFY_REDIRECT_URI = redirectUriEl.value.trim();
    if (Object.keys(updates).length) {
      try { await api.put("/api/settings", updates); } catch (_) {}
    }
    try {
      var res = await api.get("/api/spotify/auth-url");
      var win = window.open(res.auth_url, "_blank", "noopener");
      if (!win) {
        toast("Popup blocked — allow popups, or open this URL manually: " + res.auth_url, "error");
      }
      toast("Authorize Spotify in the new tab. Requesting scopes: " + res.scopes.join(", "));
      setSpotifyStatusText("Status: waiting for you to authorize in the popup…", "var(--warn, #b80)");
      startSpotifyPolling();
    } catch (e) {
      toast("Failed to start Spotify auth: " + e.message, "error");
    }
  }

  if (action === "checkSpotifyStatus") {
    try {
      var res = await api.get("/api/spotify/auth-status");
      if (res.authenticated) {
        var exp = res.expires_at ? new Date(res.expires_at * 1000).toLocaleString() : "unknown";
        toast("Spotify: connected ✅ | expires " + exp + " | scopes: " + (res.scope || "?"));
      } else {
        toast("Spotify: not connected — " + (res.reason || "unknown"), "error");
      }
    } catch (e) {
      toast("Failed to check Spotify status: " + e.message, "error");
    }
  }

  if (action === "disconnectSpotify") {
    try {
      await api.del("/api/spotify/token");
      toast("Spotify disconnected");
    } catch (e) {
      toast("Failed to disconnect Spotify: " + e.message, "error");
    }
  }
}

async function load() {
  try {
    var data = await api.get("/api/settings");
    originalValues = JSON.parse(JSON.stringify(data.settings));
    render(data.settings);
    refreshSpotifyStatus();
  } catch (e) {
    toast("Load failed: " + e.message, "error");
  }
}

function render(settings) {
  containerRef.innerHTML = "";
  containerRef.appendChild(h("h2", "Settings"));
  containerRef.appendChild(h("p", { style: { color: "var(--text-dim)" } },
    "Configure credentials and runtime options. Environment variables take priority over saved values."));

  SECTIONS.forEach(function(section) {
    containerRef.appendChild(sectionHTML(section, settings));
  });

  containerRef.appendChild(h("div", { style: { display: "flex", gap: "8px", marginTop: "16px" } },
    h("button", { onclick: load }, "Reset"),
    h("button.primary", { onclick: save }, "Save All"),
    h("button.primary", { onclick: saveAndTest, style: { background: "var(--ok)" } }, "Test All"),
  ));
}

async function save() {
  var updates = collectUpdates();
  try {
    await api.put("/api/settings", updates);
    toast("Settings saved");
    await load();
  } catch (e) {
    toast("Save failed: " + e.message, "error");
  }
}

async function saveAndTest() {
  var updates = collectUpdates();

  try {
    await api.put("/api/settings", updates);
    toast("Settings saved, testing...");
  } catch (e) {
    toast("Save failed: " + e.message, "error");
    return;
  }

  // Test all integrations
  try {
    var status = await api.get("/api/setup/status");
    var sections = [];
    var pass = 0;
    var fail = 0;
    var items = [
      { name: "Spotify", s: status.spotify },
      { name: "Jellyfin", s: status.jellyfin },
      { name: "Lidarr", s: status.lidarr },
    ];
    if (status.listenbrainz) items.push({ name: "ListenBrainz", s: status.listenbrainz });
    if (status.lastfm) items.push({ name: "Last.fm", s: status.lastfm });

    items.forEach(function(item) {
      if (item.s.configured && item.s.reachable) {
        sections.push(item.name + ": OK (" + (item.s.latency_ms || "?") + "ms)");
        pass++;
      } else if (item.s.configured && !item.s.reachable) {
        sections.push(item.name + ": CONFIGURED but unreachable -- " + (item.s.error || "no detail"));
        fail++;
      } else {
        sections.push(item.name + ": not configured");
      }
    });

    if (fail > 0) {
      toast("Test All: " + pass + " OK, " + fail + " FAILED -- " + sections.join(" | "), "error");
    } else {
      toast("Test All: " + pass + " OK -- " + sections.join(" | "));
    }
  } catch (e) {
    toast("Test failed: " + e.message, "error");
  }
}

function collectUpdates() {
  var updates = {};
  SECTIONS.forEach(function(section) {
    section.fields.forEach(function(f) {
      var el = document.getElementById(f.key);
      if (!el) return;
      if (f.type === "checkbox") {
        updates[f.key] = el.checked ? "true" : "false";
      } else {
        var val = el.value.trim();
        if (val) {
          updates[f.key] = val;
        }
      }
    });
  });
  return updates;
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div.empty", "Loading..."));
    await load();
  },
  unmount() {
    if (_spotifyPollTimer) {
      clearInterval(_spotifyPollTimer);
      _spotifyPollTimer = null;
    }
  },
};
