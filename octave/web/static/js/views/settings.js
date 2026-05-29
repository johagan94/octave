// Settings view: credential editor with auto-discovery flows.
// Secrets are masked by default with a show/hide toggle.

import { api } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let containerRef = null;
let originalValues = {};

const SECTIONS = [
  {
    title: "Spotify",
    statusId: "spotify-status",
    description: "Click 'Connect Spotify' and log in — no developer account needed. Uses PKCE OAuth (no client secret).",
    help: "Leave Client ID blank to use the bundled app. With your own Spotify app, add the redirect URI shown after Connect Spotify (the Octave external URL + /callback) in developer.spotify.com/dashboard. Client Secret is only for legacy (non-PKCE) flows.",
    fields: [
      { key: "SPOTIFY_CLIENT_ID", label: "Client ID (optional)", type: "text", required: false },
      { key: "SPOTIFY_CLIENT_SECRET", label: "Client Secret (optional)", type: "password", required: false },
      { key: "SPOTIFY_REDIRECT_URI", label: "Redirect URI", type: "text", required: false, placeholder: "https://octave.yourdomain.com/callback" },
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
    help: "API key: Jellyfin Dashboard -> API Keys. User ID: visible in URL when you click your user. Or use 'Sign in' to fill these automatically.",
    discoverId: "jellyfin-discover",
    fields: [
      { key: "JELLYFIN_URL", label: "Server URL", type: "url", required: true },
      { key: "JELLYFIN_API_KEY", label: "API Key", type: "password", required: true },
      { key: "JELLYFIN_USER_ID", label: "User ID", type: "text", required: true },
    ],
    actions: [
      { label: "Auto-detect URL", action: "jellyfinAutoDetect" },
      { label: "Sign in to Jellyfin", action: "jellyfinConnect" },
    ],
  },
  {
    title: "Lidarr",
    description: "Music management for auto-requesting missing albums.",
    help: "Optional — leave unset to disable. Find your API key in Lidarr: Settings → General → Security → API Key.",
    fields: [
      { key: "LIDARR_URL", label: "Server URL", type: "url", required: false },
      { key: "LIDARR_API_KEY", label: "API Key", type: "password", required: false },
    ],
    actions: [
      { label: "Auto-detect URL", action: "lidarrAutoDetect" },
      { label: "Open Lidarr Settings", action: "lidarrOpenSettings" },
      { label: "Validate Key", action: "lidarrValidate" },
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
    title: "Access",
    description: "External access and Subsonic/OpenSubsonic client credentials.",
    help: "External URL: the publicly-reachable address for this Octave instance (e.g. your Tailscale hostname or a reverse-proxy URL). Used by the Subsonic API to advertise the correct server URL to music clients and as the base for shareable links — leave blank to fall back to the request origin.\n\nSubsonic credentials are used by OpenSubsonic clients (Amperfy, SubStreamer, etc.). The Subsonic password is stored separately from your Jellyfin password because the Subsonic protocol requires the server to verify a one-way token against a plaintext credential. Username defaults to your Jellyfin AUTH_USERNAME if left blank.",
    fields: [
      { key: "OCTAVE_EXTERNAL_URL", label: "External / Tailscale URL", type: "url", required: false, placeholder: "https://octave.example.com" },
      { key: "SUBSONIC_USERNAME", label: "Subsonic Username", type: "text", required: false, placeholder: "same as Jellyfin username" },
      { key: "SUBSONIC_PASSWORD", label: "Subsonic Password", type: "password", required: false },
    ],
  },
  {
    title: "Security",
    description: "HTTP Basic Auth for the web interface.",
    help: "Set a password to protect Octave with HTTP Basic Auth. The browser handles the login prompt natively. Leave Password blank for open access (LAN-trust mode). Username defaults to 'octave' if left blank.",
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

// ── Field rendering ───────────────────────────────────────────────────────

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
    if (isSecret && isSet) {
      children.push(
        h("button.show-toggle", {
          onclick: (e) => {
            const inp = e.target.parentElement.querySelector('input[data-key="' + field.key + '"]');
            if (inp.type === "password") { inp.type = "text"; e.target.textContent = "hide"; }
            else { inp.type = "password"; e.target.textContent = "show"; }
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
  const fieldEls = section.fields.map(f => {
    const s = settings[f.key] || {};
    return fieldInput(f, s.value, s.source);
  });

  const actionEls = (section.actions || []).map(a =>
    h("button", { onclick: () => handleAction(a.action) }, a.label)
  );

  return h("div.card",
    h("div.card-row",
      h("h3", section.title),
    ),
    h("p", { style: { color: "var(--text-dim)", fontSize: "13px" } }, section.description),
    section.statusId
      ? h("p", { id: section.statusId, style: { fontSize: "13px", margin: "8px 0", fontWeight: "600" } }, "Status: checking…")
      : null,
    ...fieldEls,
    section.discoverId
      ? h("div", { id: section.discoverId, style: { marginTop: "8px" } })
      : null,
    actionEls.length > 0
      ? h("div", { style: { display: "flex", gap: "8px", marginTop: "8px", flexWrap: "wrap" } }, ...actionEls)
      : null,
    h("p", { style: { color: "var(--text-dim)", fontSize: "11px", marginTop: "8px" } }, section.help),
  );
}

// ── Spotify status helpers ────────────────────────────────────────────────

function setSpotifyStatusText(text, color) {
  const el = document.getElementById("spotify-status");
  if (!el) return;
  el.textContent = text;
  el.style.color = color || "var(--text-dim)";
}

async function refreshSpotifyStatus() {
  try {
    const res = await api.get("/api/spotify/auth-status");
    if (res.authenticated) {
      const exp = res.expires_at ? new Date(res.expires_at * 1000).toLocaleString() : "unknown";
      setSpotifyStatusText("Status: Connected ✅  (token valid until " + exp + ")", "var(--ok)");
    } else if (!res.client_id_available) {
      setSpotifyStatusText("Status: No Client ID available — set one below or ship a bundled default", "var(--error, #c00)");
    } else {
      const note = res.bundled_client_id ? " (using bundled app — no dev account needed)" : "";
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
  const deadline = Date.now() + 120000;
  _spotifyPollTimer = setInterval(async () => {
    const done = await refreshSpotifyStatus();
    if (done || Date.now() > deadline) {
      clearInterval(_spotifyPollTimer);
      _spotifyPollTimer = null;
      if (done) toast("Spotify connected ✅");
    }
  }, 3000);
}

// ── Jellyfin connect dialog ───────────────────────────────────────────────

function openJellyfinDialog() {
  const existingUrl = (document.getElementById("JELLYFIN_URL") || {}).value || "";

  // Build dialog DOM
  let usernameEl, passwordEl, urlEl, statusEl, libraryRow, librarySelect;
  let pendingCreds = null; // { api_key, user_id, url }

  const dialog = h("dialog",
    h("h3", { style: { margin: "0 0 16px" } }, "Sign in to Jellyfin"),

    h("div.setting-field",
      h("label", { for: "jf-url" }, "Server URL"),
      urlEl = h("input", { id: "jf-url", type: "url", value: existingUrl, placeholder: "http://jellyfin:8096", autocomplete: "off" }),
    ),
    h("div.setting-field",
      h("label", { for: "jf-username" }, "Username"),
      usernameEl = h("input", { id: "jf-username", type: "text", autocomplete: "username", placeholder: "admin" }),
    ),
    h("div.setting-field",
      h("label", { for: "jf-password" }, "Password"),
      passwordEl = h("input", { id: "jf-password", type: "password", autocomplete: "current-password" }),
    ),

    statusEl = h("p", { style: { fontSize: "13px", minHeight: "20px", margin: "8px 0", color: "var(--text-dim)" } }),

    // Library picker — hidden until after successful auth
    libraryRow = h("div", { style: { display: "none", marginTop: "8px" } },
      h("div.setting-field",
        h("label", { for: "jf-library" }, "Music Library"),
        librarySelect = h("select", { id: "jf-library" }),
      ),
      h("p", { style: { fontSize: "11px", color: "var(--text-dim)", margin: "4px 0 0" } },
        "Octave will sync playlists into this library. The ID is saved to config.json."),
    ),

    h("menu",
      h("button", {
        onclick: () => dialog.close(),
        style: { marginRight: "auto" },
      }, "Cancel"),
      h("button.primary", {
        id: "jf-submit",
        onclick: () => doJellyfinAuth(urlEl, usernameEl, passwordEl, statusEl, libraryRow, librarySelect, dialog),
      }, "Sign in"),
    ),
  );

  // Submit on Enter
  dialog.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !libraryRow.style.display !== "none") {
      doJellyfinAuth(urlEl, usernameEl, passwordEl, statusEl, libraryRow, librarySelect, dialog);
    }
  });

  document.body.appendChild(dialog);
  dialog.showModal();
  usernameEl.focus();
}

async function doJellyfinAuth(urlEl, usernameEl, passwordEl, statusEl, libraryRow, librarySelect, dialog) {
  const url      = urlEl.value.trim();
  const username = usernameEl.value.trim();
  const password = passwordEl.value;

  if (!url || !username) {
    statusEl.textContent = "Server URL and username are required.";
    statusEl.style.color = "var(--error)";
    return;
  }

  const btn = document.getElementById("jf-submit");
  if (btn) btn.disabled = true;
  statusEl.textContent = "Authenticating…";
  statusEl.style.color = "var(--text-dim)";

  try {
    const res = await api.post("/api/discover/jellyfin/connect", { url, username, password });

    // Fill API key + User ID fields in the main form
    const urlField = document.getElementById("JELLYFIN_URL");
    const keyField = document.getElementById("JELLYFIN_API_KEY");
    const uidField = document.getElementById("JELLYFIN_USER_ID");
    if (urlField) urlField.value = url;
    if (keyField) { keyField.value = res.api_key; keyField.type = "text"; }
    if (uidField) uidField.value = res.user_id;

    statusEl.textContent = "Signed in as " + res.display_name + " ✅";
    statusEl.style.color = "var(--ok)";

    // Populate library picker
    const libs = res.music_libraries && res.music_libraries.length
      ? res.music_libraries
      : res.libraries || [];

    if (libs.length > 0) {
      librarySelect.innerHTML = "";
      libs.forEach(lib => {
        librarySelect.appendChild(h("option", { value: lib.id }, lib.name + (lib.type !== "music" ? " (" + lib.type + ")" : "")));
      });
      libraryRow.style.display = "block";

      // Change the Submit button to "Apply & Close"
      if (btn) {
        btn.textContent = "Apply & Close";
        btn.disabled = false;
        btn.onclick = async () => {
          await applyJellyfinLibrary(url, res.api_key, res.user_id, librarySelect.value, dialog);
        };
      }
    } else {
      // No libraries found — just close
      if (btn) {
        btn.textContent = "Done";
        btn.disabled = false;
        btn.onclick = () => dialog.close();
      }
      toast("Signed in as " + res.display_name + ". Save settings to persist.", "ok");
    }
  } catch (e) {
    statusEl.textContent = e.message || "Authentication failed";
    statusEl.style.color = "var(--error)";
    if (btn) btn.disabled = false;
  }
}

async function applyJellyfinLibrary(url, apiKey, userId, libraryId, dialog) {
  try {
    // Save credentials to settings.json
    await api.put("/api/settings", {
      JELLYFIN_URL:     url,
      JELLYFIN_API_KEY: apiKey,
      JELLYFIN_USER_ID: userId,
    });

    // Update music_library_id in config.json
    const cfgData = await api.get("/api/config");
    const cfg = cfgData.config || {};
    cfg.jellyfin = cfg.jellyfin || {};
    cfg.jellyfin.music_library_id = libraryId;
    await api.put("/api/config", cfg);

    toast("Jellyfin connected and library saved ✅");
    dialog.close();
    await load(); // refresh settings display
  } catch (e) {
    toast("Failed to save: " + e.message, "error");
  }
}

// ── Action handlers ───────────────────────────────────────────────────────

async function handleAction(action) {
  // ── Spotify ──
  if (action === "connectSpotify") {
    const clientIdEl   = document.getElementById("SPOTIFY_CLIENT_ID");
    const redirectUriEl = document.getElementById("SPOTIFY_REDIRECT_URI");
    const updates = {};
    if (clientIdEl && clientIdEl.value.trim())   updates.SPOTIFY_CLIENT_ID   = clientIdEl.value.trim();
    if (redirectUriEl && redirectUriEl.value.trim()) updates.SPOTIFY_REDIRECT_URI = redirectUriEl.value.trim();
    if (Object.keys(updates).length) {
      try { await api.put("/api/settings", updates); } catch (_) {}
    }
    try {
      const res = await api.get("/api/spotify/auth-url");
      const win = window.open(res.auth_url, "_blank", "noopener");
      if (!win) {
        toast("Popup blocked — allow popups, or open manually: " + res.auth_url, "error");
      }
      toast("Authorize Spotify in the new tab. Scopes: " + res.scopes.join(", "));
      setSpotifyStatusText("Status: waiting for you to authorize in the popup…", "var(--warn, #b80)");
      startSpotifyPolling();
    } catch (e) {
      toast("Failed to start Spotify auth: " + e.message, "error");
    }
  }

  if (action === "checkSpotifyStatus") {
    try {
      const res = await api.get("/api/spotify/auth-status");
      if (res.authenticated) {
        const exp = res.expires_at ? new Date(res.expires_at * 1000).toLocaleString() : "unknown";
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
      refreshSpotifyStatus();
    } catch (e) {
      toast("Failed to disconnect Spotify: " + e.message, "error");
    }
  }

  // ── Jellyfin ──
  if (action === "jellyfinAutoDetect") {
    toast("Scanning for Jellyfin…");
    try {
      const res = await api.get("/api/discover/services");
      if (res.jellyfin && res.jellyfin.length > 0) {
        const url = res.jellyfin[0];
        const el = document.getElementById("JELLYFIN_URL");
        if (el) el.value = url;
        toast("Found Jellyfin at " + url);
      } else {
        toast("No Jellyfin found on common addresses — enter URL manually", "error");
      }
    } catch (e) {
      toast("Auto-detect failed: " + e.message, "error");
    }
  }

  if (action === "jellyfinConnect") {
    openJellyfinDialog();
  }

  // ── Lidarr ──
  if (action === "lidarrAutoDetect") {
    toast("Scanning for Lidarr…");
    try {
      const res = await api.get("/api/discover/services");
      if (res.lidarr && res.lidarr.length > 0) {
        const url = res.lidarr[0];
        const el = document.getElementById("LIDARR_URL");
        if (el) el.value = url;
        toast("Found Lidarr at " + url);
      } else {
        toast("No Lidarr found on common addresses — enter URL manually", "error");
      }
    } catch (e) {
      toast("Auto-detect failed: " + e.message, "error");
    }
  }

  if (action === "lidarrOpenSettings") {
    const urlEl = document.getElementById("LIDARR_URL");
    const base = (urlEl && urlEl.value.trim()) || "";
    if (!base) {
      toast("Enter (or auto-detect) the Lidarr URL first", "error");
      return;
    }
    window.open(base.replace(/\/$/, "") + "/settings/general", "_blank", "noopener");
  }

  if (action === "lidarrValidate") {
    const urlEl    = document.getElementById("LIDARR_URL");
    const keyEl    = document.getElementById("LIDARR_API_KEY");
    const url      = (urlEl && urlEl.value.trim()) || "";
    const api_key  = (keyEl && keyEl.value.trim()) || "";
    if (!url || !api_key) {
      toast("Enter URL and API key first", "error");
      return;
    }
    try {
      const res = await api.post("/api/discover/lidarr/validate", { url, api_key });
      toast("Lidarr ✅ — version " + res.version + " (" + res.branch + ")");
    } catch (e) {
      toast("Lidarr validation failed: " + e.message, "error");
    }
  }
}

// ── Load / save / render ──────────────────────────────────────────────────

async function load() {
  try {
    const data = await api.get("/api/settings");
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

  SECTIONS.forEach(section => {
    containerRef.appendChild(sectionHTML(section, settings));
  });

  // ── Last.fm history import ─────────────────────────────────────────────────
  containerRef.appendChild(buildLastFmImportCard());

  containerRef.appendChild(h("div", { style: { display: "flex", gap: "8px", marginTop: "16px" } },
    h("button", { onclick: load }, "Reset"),
    h("button.primary", { onclick: save }, "Save All"),
    h("button.primary", { onclick: saveAndTest, style: { background: "var(--ok)" } }, "Test All"),
  ));
}

// ── Last.fm history import card ────────────────────────────────────────────────

let _historyPollTimer = null;

function buildLastFmImportCard() {
  let statusEl;

  async function refreshImportStatus() {
    try {
      const res = await api.get("/api/sync/lastfm_history/status");
      if (!statusEl) return;
      const s = res.status || "idle";
      if (s === "idle") {
        statusEl.textContent = "Not yet imported.";
        statusEl.style.color = "var(--text-dim)";
        stopHistoryPoll();
      } else if (s === "running") {
        const pg = res.current_page || 0;
        const tot = res.total_pages || "?";
        const imported = res.imported_count || 0;
        const matched = res.matched || 0;
        statusEl.textContent = `Importing… page ${pg}/${tot} — ${imported} scrobbles, ${matched} matched`;
        statusEl.style.color = "var(--accent)";
      } else if (s === "done") {
        statusEl.textContent = `Done ✅ — ${res.matched} / ${res.imported_count} scrobbles matched to Jellyfin tracks`;
        statusEl.style.color = "var(--ok)";
        stopHistoryPoll();
      } else if (s === "error") {
        statusEl.textContent = `Error: ${res.error}`;
        statusEl.style.color = "var(--error, #c00)";
        stopHistoryPoll();
      }
    } catch (e) { /* ignore */ }
  }

  function stopHistoryPoll() {
    if (_historyPollTimer) { clearInterval(_historyPollTimer); _historyPollTimer = null; }
  }

  async function startImport(fromScratch) {
    try {
      const body = fromScratch ? { from_ts: 0 } : {};
      await api.post("/api/sync/lastfm_history", body);
      toast("Last.fm import started");
      stopHistoryPoll();
      _historyPollTimer = setInterval(refreshImportStatus, 4000);
      await refreshImportStatus();
    } catch (e) {
      if (e.status === 409) toast("Import already running", "warn");
      else toast(`Start failed: ${e.message}`, "error");
    }
  }

  // Kick off a status check immediately so the card shows current state on load
  setTimeout(refreshImportStatus, 100);

  return h("div.card",
    h("div.card-row",
      h("h3", "Last.fm → Jellyfin history import"),
    ),
    h("p", { style: { color: "var(--text-dim)", fontSize: "13px" } },
      "Pull your Last.fm scrobble history and write backdated play counts into Jellyfin. " +
      "Requires Last.fm API Key and Username above. Large libraries may take several minutes."),
    statusEl = h("p", { style: { fontSize: "13px", minHeight: "20px", margin: "8px 0", color: "var(--text-dim)" } },
      "Checking…"),
    h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
      h("button.primary", { onclick: () => startImport(false) }, "Import new scrobbles"),
      h("button", {
        onclick: () => {
          if (!confirm("Re-import ALL Last.fm history from the beginning? This may create duplicate play counts if you've imported before.")) return;
          startImport(true);
        },
      }, "Import all (from scratch)"),
    ),
    h("p", { style: { color: "var(--text-dim)", fontSize: "11px", marginTop: "8px" } },
      '"Import new scrobbles" resumes from the last imported timestamp. ' +
      '"Import all" reimports everything — use cautiously as it may inflate play counts.'),
  );
}

async function save() {
  const updates = collectUpdates();
  try {
    await api.put("/api/settings", updates);
    toast("Settings saved");
    await load();
  } catch (e) {
    toast("Save failed: " + e.message, "error");
  }
}

async function saveAndTest() {
  const updates = collectUpdates();
  try {
    await api.put("/api/settings", updates);
    toast("Settings saved, testing...");
  } catch (e) {
    toast("Save failed: " + e.message, "error");
    return;
  }

  try {
    const status = await api.get("/api/setup/status");
    const items = [
      { name: "Spotify",  s: status.spotify },
      { name: "Jellyfin", s: status.jellyfin },
      { name: "Lidarr",   s: status.lidarr },
    ];
    if (status.listenbrainz) items.push({ name: "ListenBrainz", s: status.listenbrainz });
    if (status.lastfm)       items.push({ name: "Last.fm",      s: status.lastfm });

    let pass = 0, fail = 0;
    const sections = items.map(item => {
      if (item.s.configured && item.s.reachable)  { pass++; return item.name + ": OK (" + (item.s.latency_ms || "?") + "ms)"; }
      if (item.s.configured && !item.s.reachable) { fail++; return item.name + ": FAIL — " + (item.s.error || "no detail"); }
      return item.name + ": not configured";
    });

    if (fail > 0) {
      toast("Test: " + pass + " OK, " + fail + " FAILED — " + sections.join(" | "), "error");
    } else {
      toast("Test: " + pass + " OK — " + sections.join(" | "));
    }
  } catch (e) {
    toast("Test failed: " + e.message, "error");
  }
}

function collectUpdates() {
  const updates = {};
  SECTIONS.forEach(section => {
    section.fields.forEach(f => {
      const el = document.getElementById(f.key);
      if (!el) return;
      if (f.type === "checkbox") {
        updates[f.key] = el.checked ? "true" : "false";
      } else {
        // Send empty string to clear a previously saved setting
        updates[f.key] = el.value.trim();
      }
    });
  });
  return updates;
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div.empty", "Loading…"));
    await load();
  },
  unmount() {
    if (_spotifyPollTimer) {
      clearInterval(_spotifyPollTimer);
      _spotifyPollTimer = null;
    }
    // Clean up any open dialogs
    document.querySelectorAll("dialog[open]").forEach(d => d.close());
  },
};
