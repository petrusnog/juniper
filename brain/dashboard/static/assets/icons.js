// Menu icons: 24x24 line icons drawn with strokes, so they follow the text color (`currentColor`)
// and the theme. Built with the DOM (never as HTML), like every other node of the dashboard.

const NS = "http://www.w3.org/2000/svg";

// Each icon is a list of shapes: [tag, attributes].
const SHAPES = {
  visao: [["rect", { x: 3, y: 3, width: 7, height: 9 }], ["rect", { x: 14, y: 3, width: 7, height: 5 }], ["rect", { x: 14, y: 12, width: 7, height: 9 }], ["rect", { x: 3, y: 16, width: 7, height: 5 }]],
  memorias: [["ellipse", { cx: 12, cy: 5, rx: 9, ry: 3 }], ["path", { d: "M3 5v14a9 3 0 0 0 18 0V5" }], ["path", { d: "M3 12a9 3 0 0 0 18 0" }]],
  central: [["circle", { cx: 12, cy: 12, r: 10 }], ["circle", { cx: 12, cy: 12, r: 6 }], ["circle", { cx: 12, cy: 12, r: 2 }]],
  estudos: [["path", { d: "M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" }], ["path", { d: "M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" }]],
  sonhos: [["path", { d: "M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z" }]],
  conversas: [["path", { d: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" }]],
  ocorrencias: [["path", { d: "m21.7 18-8-14a2 2 0 0 0-3.5 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3z" }], ["path", { d: "M12 9v4" }], ["path", { d: "M12 17h.01" }]],
  pesquisa: [["circle", { cx: 11, cy: 11, r: 8 }], ["path", { d: "m21 21-4.3-4.3" }]],
  afetos: [["path", { d: "M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7z" }]],
  tarefas: [["circle", { cx: 12, cy: 12, r: 10 }], ["path", { d: "m10 8 6 4-6 4z" }]],
  logs: [["path", { d: "m4 17 6-6-6-6" }], ["path", { d: "M12 19h8" }]],
  saude: [["path", { d: "M22 12h-4l-3 9L9 3l-3 9H2" }]],
  usuarios: [["path", { d: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" }], ["circle", { cx: 9, cy: 7, r: 4 }], ["path", { d: "M22 21v-2a4 4 0 0 0-3-3.9" }], ["path", { d: "M16 3.1a4 4 0 0 1 0 7.8" }]],
};

// A neutral dot for a page that has no icon yet, so the menu keeps its alignment.
const FALLBACK = [["circle", { cx: 12, cy: 12, r: 3 }]];

export function icon(name) {
  const node = document.createElementNS(NS, "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", class: "nav-icon", "aria-hidden": "true", focusable: "false" })) {
    node.setAttribute(key, value);
  }
  for (const [tag, attrs] of SHAPES[name] || FALLBACK) {
    const shape = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) shape.setAttribute(key, String(value));
    node.append(shape);
  }
  return node;
}

export const ICON_NAMES = Object.keys(SHAPES);
