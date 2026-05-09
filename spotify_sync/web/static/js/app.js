// Entry point. Registers views, starts the router, wires API-key dialog.

import { register, start } from "./router.js";
import { getApiKey, setApiKey } from "./api.js";

import dashboard from "./views/dashboard.js";
import playlists from "./views/playlists.js";
import logs      from "./views/logs.js";
import setup     from "./views/setup.js";
import config    from "./views/config.js";

register("dashboard", dashboard);
register("playlists", playlists);
register("logs",      logs);
register("setup",     setup);
register("config",    config);

// API-key dialog
const dialog = document.getElementById("api-key-dialog");
const input  = document.getElementById("api-key-input");
const saveBtn = document.getElementById("api-key-save");

document.getElementById("api-key-btn").addEventListener("click", () => {
  input.value = getApiKey();
  dialog.showModal();
});

saveBtn.addEventListener("click", () => {
  setApiKey(input.value.trim());
  // Reload so all views re-fetch with the new key
  location.reload();
});

// Default route
if (!location.hash) location.hash = "#/dashboard";

start(document.getElementById("view"));
