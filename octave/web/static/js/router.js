// Hash-based router. Each route module exports {mount(container), unmount()}.

const routes = new Map();
let current = null;
let currentName = null;
let containerEl = null;

export function register(name, module) {
  routes.set(name, module);
}

export function navigate(name) {
  if (location.hash !== `#/${name}`) {
    location.hash = `#/${name}`;
  } else {
    apply();
  }
}

async function apply() {
  const hash = location.hash || "#/dashboard";
  const name = hash.replace(/^#\/?/, "") || "dashboard";

  // Highlight active nav
  for (const a of document.querySelectorAll("nav a")) {
    a.classList.toggle("active", a.dataset.route === name);
  }

  if (current && current.unmount) {
    try { current.unmount(); } catch (e) { console.error("unmount error:", e); }
  }

  const next = routes.get(name) || routes.get("dashboard");
  current = next;
  currentName = name;
  containerEl.innerHTML = "";
  try {
    await next.mount(containerEl);
  } catch (e) {
    console.error(`mount(${name}) error:`, e);
    containerEl.innerHTML = `<div class="card"><h2>Error</h2><pre>${escape(String(e.stack || e))}</pre></div>`;
  }
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

export function start(container) {
  containerEl = container;
  window.addEventListener("hashchange", apply);
  apply();
}

export function currentRoute() { return currentName; }
