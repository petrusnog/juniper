// Small helpers to build the page. Data is always put in with textContent, never as HTML.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

const TIME = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});
const DAY = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit" });

export function when(iso) {
  return iso ? TIME.format(new Date(iso)) : "—";
}

export function day(iso) {
  return iso ? DAY.format(new Date(iso)) : "—";
}

export function ago(iso) {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "agora há pouco";
  if (seconds < 5400) return `há ${Math.round(seconds / 60)} min`;
  if (seconds < 129600) return `há ${Math.round(seconds / 3600)} h`;
  return `há ${Math.round(seconds / 86400)} dias`;
}

export function percent(value) {
  return `${Math.round(value * 100)}%`;
}

export function card(title, ...children) {
  return h("section", { class: "card" }, title ? h("h2", { text: title }) : null, ...children);
}

export function badge(text, kind = "") {
  return h("span", { class: `badge ${kind}`.trim(), text });
}

export function empty(text) {
  return h("p", { class: "muted empty", text });
}

export function bar(fraction, kind = "") {
  const fill = h("span", { class: `fill ${kind}`.trim() });
  fill.style.width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  return h("span", { class: "bar", role: "presentation" }, fill);
}

// A bar that goes both ways from the middle (-1..1), for valence and mood.
export function signedBar(value) {
  const fill = h("span", { class: `fill ${value < 0 ? "neg" : "pos"}` });
  const size = Math.min(1, Math.abs(value)) * 50;
  fill.style.width = `${size}%`;
  fill.style.left = value < 0 ? `${50 - size}%` : "50%";
  return h("span", { class: "bar signed", role: "presentation" }, fill);
}
