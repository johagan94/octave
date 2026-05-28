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

const _reduceMotion = window.matchMedia
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function apply() {
  const hash = location.hash || "#/dashboard";
  const name = hash.replace(/^#\/?/, "") || "dashboard";

  // Highlight active nav (instant — never gated behind the transition)
  for (const a of document.querySelectorAll("nav a")) {
    a.classList.toggle("active", a.dataset.route === name);
  }

  const next = routes.get(name) || routes.get("dashboard");

  // The visual swap: tear down the old view, paint the new view's first
  // frame. We deliberately do NOT await mount() here — the view paints its
  // placeholder synchronously and fills in as data arrives, so the cross-fade
  // is never blocked on a slow network fetch.
  const onMountError = (e) => {
    console.error(`mount(${name}) error:`, e);
    containerEl.innerHTML = `<div class="card"><h2>Error</h2><pre>${escape(String(e.stack || e))}</pre></div>`;
  };
  const swap = () => {
    if (current && current.unmount) {
      try { current.unmount(); } catch (e) { console.error("unmount error:", e); }
    }
    current = next;
    currentName = name;
    containerEl.innerHTML = "";
    // Call mount synchronously so its first paint (the view's placeholder)
    // lands before the transition snapshots the new state — otherwise the
    // cross-fade captures an empty container. We don't await the returned
    // promise; async data fills in after the transition completes.
    try {
      const r = next.mount(containerEl);
      if (r && typeof r.catch === "function") r.catch(onMountError);
    } catch (e) {
      onMountError(e);
    }
  };

  if (document.startViewTransition && !_reduceMotion) {
    document.startViewTransition(swap);
  } else {
    swap();
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
