import { api } from "/assets/api.js";
import { ago, badge, card, clear, empty, h } from "/assets/ui.js";

const state = { q: "", types: new Set() };
const TYPES = [
  ["memorias", "Memórias"], ["crencas", "Crenças"], ["temas", "Temas"],
  ["notas", "Notas"], ["afetos", "Sentimentos"], ["conversas", "Conversas"],
];

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function results(data) {
  const box = h("div", { "aria-live": "polite" });
  if (data.warning) box.append(h("p", { class: "muted small", text: data.warning }));
  if (!data.total) {
    box.append(empty(`Nada encontrado para “${data.query}”.`));
    return box;
  }
  box.append(h("p", { class: "muted small", text: `${data.total} resultado(s)${data.semantic ? " (por palavras e por significado)" : " (só por palavras)"}.` }));
  for (const group of data.groups) {
    if (!group.items.length) continue;
    box.append(h("h2", { class: "section-title", text: `${group.label} (${group.items.length})` }));
    const list = h("div", {});
    for (const item of group.items) {
      list.append(
        h("a", { class: "mem-item", href: item.link },
          h("span", { class: "mem-text", text: item.text }),
          h("span", { class: "mem-meta" }, badge(item.kind), badge(item.match === "texto" ? "palavras" : `significado ${item.score.toFixed(2)}`, item.match === "texto" ? "" : "live"), h("span", { class: "muted small", text: ago(item.at) })),
        ),
      );
    }
    box.append(card("", list));
  }
  return box;
}

export async function render() {
  const out = h("div", {});
  const filters = h("div", { class: "chips", role: "group", "aria-label": "Onde procurar" });
  const input = h("input", { type: "search", placeholder: "Procure por uma palavra ou ideia…", "aria-label": "O que procurar", maxlength: "200", value: state.q });

  async function run() {
    state.q = input.value.trim();
    drawFilters();
    if (!state.q) { clear(out); return; }
    clear(out).append(h("p", { class: "muted", text: "Procurando…" }));
    try {
      const data = await api("/search", { q: state.q, type: [...state.types] });
      clear(out).append(results(data));
    } catch (error) {
      clear(out).append(h("p", { class: "error", role: "alert", text: error.message }));
    }
  }

  function drawFilters() {
    clear(filters).append(
      chip("Tudo", state.types.size === 0, () => { state.types.clear(); run(); }),
      ...TYPES.map(([id, label]) => chip(label, state.types.has(id), () => { state.types.has(id) ? state.types.delete(id) : state.types.add(id); run(); })),
    );
  }

  drawFilters();
  const form = h("form", { onsubmit: (event) => { event.preventDefault(); run(); } }, input, h("button", { type: "submit", text: "Procurar" }));
  if (state.q) run();
  return h("div", {}, h("h1", { text: "Pesquisa" }), card("", form, filters), out);
}
