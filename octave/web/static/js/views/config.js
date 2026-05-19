// Config view: raw JSON editor with parse-validate-then-save.

import { api } from "../api.js";
import { h } from "../h.js";
import { toast } from "../toast.js";

let containerRef = null;
let textarea = null;

async function load() {
  try {
    const data = await api.get("/api/config");
    textarea.value = JSON.stringify(data.config, null, 2);
  } catch (e) {
    toast(`Load failed: ${e.message}`, "error");
  }
}

async function save() {
  let parsed;
  try {
    parsed = JSON.parse(textarea.value);
  } catch (e) {
    toast(`Invalid JSON: ${e.message}`, "error");
    return;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    toast("Top-level must be an object", "error");
    return;
  }
  try {
    await api.put("/api/config", parsed);
    toast("Config saved");
  } catch (e) {
    toast(`Save failed: ${e.message}`, "error");
  }
}

export default {
  async mount(container) {
    containerRef = container;
    container.appendChild(h("div",
      h("h2", "Configuration"),
      h("p", { style: { color: "var(--text-dim)" } },
        "Raw ", h("code", "config.json"), " editor. Credentials are env vars — they don't appear here."),
      h("div.card",
        textarea = h("textarea", { spellcheck: false, autocomplete: "off", placeholder: "Loading…" }),
        h("div.card-row", { style: { marginTop: "12px" } },
          h("small", { style: { color: "var(--text-dim)" } },
            "Saving overwrites the file atomically (temp + rename). The next sync uses the new values."),
          h("div", { style: { display: "flex", gap: "8px" } },
            h("button", { onclick: load }, "Reload"),
            h("button.primary", { onclick: save }, "Save"),
          ),
        ),
      ),
    ));
    await load();
  },
  unmount() {},
};
