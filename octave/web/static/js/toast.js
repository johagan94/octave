// Tiny toast notifier. One singleton element appended to <body>.

let el = null;
let hideTimer = 0;

function ensure() {
  if (el) return el;
  el = document.createElement("div");
  el.id = "toast";
  document.body.appendChild(el);
  return el;
}

export function toast(message, kind = "ok", ms = 3500) {
  const node = ensure();
  node.textContent = message;
  node.className = `show ${kind}`;
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => { node.className = ""; }, ms);
}
