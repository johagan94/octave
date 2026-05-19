// Entry point. Registers views and starts the router.
// Auth is handled by the browser via HTTP Basic Auth (no JS involvement needed).

import { register, start } from "./router.js";

import dashboard from "./views/dashboard.js";
import playlists from "./views/playlists.js";
import logs      from "./views/logs.js";
import setup     from "./views/setup.js";
import config    from "./views/config.js";
import missing   from "./views/missing.js";
import settings  from "./views/settings.js";

register("dashboard", dashboard);
register("playlists", playlists);
register("logs",      logs);
register("setup",     setup);
register("config",    config);
register("missing",   missing);
register("settings",  settings);

// Default route
if (!location.hash) location.hash = "#/dashboard";

start(document.getElementById("view"));
