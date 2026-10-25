// Tiny SVG charts (no library). Colors come from CSS classes, so they follow the theme.

const NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  for (const child of children) node.append(child);
  return node;
}

function text(x, y, content, cls, anchor = "middle") {
  const node = svg("text", { x, y, class: cls, "text-anchor": anchor });
  node.textContent = content;
  return node;
}

// points: [{at: ISO, value: -1..1}]. Draws the line over a zero axis.
export function lineChart(points, { min = -1, max = 1, label = "gráfico" } = {}) {
  // as wide as the screen lets it be, so the text stays readable on a phone
  const host = document.getElementById("view");
  const width = Math.min(720, Math.max(300, (host ? host.clientWidth : 640) - 34)), height = 200, pad = { l: 34, r: 10, t: 10, b: 22 };
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": label,
  });
  const innerW = width - pad.l - pad.r, innerH = height - pad.t - pad.b;
  const y = (v) => pad.t + innerH * (1 - (v - min) / (max - min));
  for (const tick of [min, (min + max) / 2, max]) {
    root.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: y(tick), y2: y(tick), class: tick === 0 ? "axis zero" : "axis" }));
    root.append(text(pad.l - 6, y(tick) + 4, tick.toFixed(1), "tick", "end"));
  }
  if (points.length < 2) return root;
  const t0 = new Date(points[0].at).getTime();
  const span = Math.max(1, new Date(points.at(-1).at).getTime() - t0);
  const x = (iso) => pad.l + (innerW * (new Date(iso).getTime() - t0)) / span;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.at).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${path} L${x(points.at(-1).at).toFixed(1)},${y(0)} L${x(points[0].at).toFixed(1)},${y(0)} Z`;
  root.append(svg("path", { d: area, class: "area" }), svg("path", { d: path, class: "line" }));
  const first = new Date(points[0].at), last = new Date(points.at(-1).at);
  const fmt = (d) => d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  root.append(text(pad.l, height - 4, fmt(first), "tick", "start"), text(width - pad.r, height - 4, fmt(last), "tick", "end"));
  return root;
}

// traits: [{name, value, baseline, min, max}] on a 0..1 scale.
export function radarChart(traits, { label = "traços" } = {}) {
  const size = 320, c = size / 2, r = 110;
  const root = svg("svg", { viewBox: `-50 0 ${size + 100} ${size}`, class: "chart radar", role: "img", "aria-label": label });
  const n = traits.length;
  if (n < 3) return root;
  const point = (i, v) => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [c + Math.cos(angle) * r * v, c + Math.sin(angle) * r * v];
  };
  for (const ring of [0.25, 0.5, 0.75, 1]) {
    root.append(svg("polygon", { points: traits.map((_, i) => point(i, ring).join(",")).join(" "), class: "grid" }));
  }
  traits.forEach((t, i) => {
    const [x, y] = point(i, 1);
    root.append(svg("line", { x1: c, y1: c, x2: x, y2: y, class: "axis" }));
    const [lx, ly] = point(i, 1.18);
    root.append(text(lx, ly + 4, t.name, "tick", lx < c - 8 ? "end" : lx > c + 8 ? "start" : "middle"));
  });
  const shape = (key, cls) =>
    root.append(svg("polygon", { points: traits.map((t, i) => point(i, t[key]).join(",")).join(" "), class: cls }));
  shape("baseline", "baseline");
  shape("value", "now");
  return root;
}
