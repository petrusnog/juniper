import { api } from "/assets/api.js";
import { dangerAction } from "/assets/actions.js";
import { badge, bar, card, clear, empty, h, when } from "/assets/ui.js";

const UNCERTAINTY = {
  baixa: ["firme", ""],
  média: ["incerteza média", "warn"],
  alta: ["incerta", "warn"],
};
const PAGE = 25;
const all = { interest: "", uncertainty: "", source: "", kind: "", q: "", offset: 0 };

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function noteItem(n, showTopic) {
  const [label, kind] = UNCERTAINTY[n.uncertainty] || [n.uncertainty, ""];
  const question = n.kind === "pergunta";
  return h("li", { class: `note ${question ? "question" : ""}`.trim() },
    h("span", { class: "note-text", text: n.text }),
    h("span", { class: "note-meta" },
      question ? badge("pergunta em aberto", "live") : badge(label, kind),
      badge(n.source === "documento" ? "documento" : "modelo", n.source === "documento" ? "live" : ""),
      h("span", { class: "muted small", text: `passo ${n.level} · ${when(n.created_at)}` }),
      showTopic && n.topic ? h("a", { class: "small", href: `#/estudos/${n.interest_id}`, text: n.topic }) : null,
    ),
  );
}

function stepsChart(steps) {
  if (!steps.length) return empty("Ainda não há notas em nenhum passo.");
  const most = Math.max(...steps.map((s) => s.notes));
  return h("ul", { class: "plain" }, ...steps.map((s) =>
    h("li", {}, h("span", { class: "kind", text: `passo ${s.level}` }), bar(s.notes / most), h("span", { class: "muted small", text: `${s.notes} nota(s)` }))));
}

async function topic(id) {
  const t = await api(`/interests/${encodeURIComponent(id)}`);
  const view = { uncertainty: "", source: "", q: "" };
  const list = h("ul", { class: "plain notes" });
  const filters = h("div", {});
  const search = h("input", { type: "search", placeholder: "Buscar nas notas…", "aria-label": "Buscar nas notas" });
  const claims = t.notes_list.filter((n) => n.kind === "afirmacao").sort((a, b) => b.level - a.level || a.created_at.localeCompare(b.created_at));
  const questions = t.notes_list.filter((n) => n.kind === "pergunta");

  function draw() {
    const needle = view.q.toLowerCase();
    const shown = claims.filter((n) =>
      (!view.uncertainty || n.uncertainty === view.uncertainty) &&
      (!view.source || n.source === view.source) &&
      (!needle || n.text.toLowerCase().includes(needle)));
    const pick = (key, value) => () => { view[key] = value; draw(); };
    clear(filters).append(
      h("div", { class: "chips", role: "group", "aria-label": "Incerteza" },
        ...[["", "Todas"], ["baixa", "Firmes"], ["média", "Incerteza média"], ["alta", "Incertas"]].map(([v, l]) => chip(l, view.uncertainty === v, pick("uncertainty", v)))),
      h("div", { class: "chips", role: "group", "aria-label": "Fonte" },
        ...[["", "Qualquer fonte"], ["modelo", "Do modelo"], ["documento", "De documento"]].map(([v, l]) => chip(l, view.source === v, pick("source", v)))),
    );
    clear(list);
    if (!shown.length) list.append(empty(claims.length ? "Nenhuma nota com esses filtros." : "Ainda não há notas."));
    for (const n of shown) list.append(noteItem(n, false));
  }
  search.addEventListener("input", () => { view.q = search.value.trim(); draw(); });
  draw();

  return h("div", {},
    h("p", {}, h("a", { href: "#/estudos", text: "‹ Todos os temas" })),
    h("h1", { text: t.topic }),
    card("",
      h("p", {}, badge(`passo ${t.level}`), " ", badge(t.stance_words, t.stance === "dislike" ? "warn" : ""), h("span", { class: "muted small", text: `  intensidade ${t.intensity.toFixed(2)} · estudado pela última vez ${t.last_studied_at ? when(t.last_studied_at) : "nunca"} · ${t.exposures} contato(s)` })),
      t.reasons.length ? h("p", {}, h("span", { class: "label", text: "Por quê: " }), t.reasons.join("; ")) : null,
    ),
    card("Ações", dangerAction("Esquecer este tema", t.id, `/memory/interests/${t.id}/forget`, () => { window.location.hash = "#/estudos"; })),
    card("Notas por passo", stepsChart(t.steps)),
    questions.length ? card("Perguntas em aberto para o próximo passo", h("ul", { class: "plain notes" }, ...questions.map((q) => noteItem(q, false)))) : null,
    card(`Notas (${claims.length})`, search, filters, list),
  );
}

async function everyNote() {
  const filters = h("div", {});
  const list = h("ul", { class: "plain notes" });
  const pager = h("div", { class: "pager" });
  const search = h("input", { type: "search", placeholder: "Buscar em todas as notas…", "aria-label": "Buscar em todas as notas", value: all.q });
  async function draw() {
    const data = await api("/notes", { ...all, limit: PAGE });
    const pick = (key, value) => async () => { all[key] = value; all.offset = 0; await draw(); };
    clear(filters).append(
      h("div", { class: "chips", role: "group", "aria-label": "Tipo" },
        ...[["", "Notas e perguntas"], ["afirmacao", "Só notas"], ["pergunta", "Só perguntas"]].map(([v, l]) => chip(l, all.kind === v, pick("kind", v)))),
      h("div", { class: "chips", role: "group", "aria-label": "Incerteza" },
        ...[["", "Qualquer incerteza"], ["baixa", "Firmes"], ["média", "Média"], ["alta", "Incertas"]].map(([v, l]) => chip(l, all.uncertainty === v, pick("uncertainty", v)))),
      h("div", { class: "chips", role: "group", "aria-label": "Fonte" },
        ...[["", "Qualquer fonte"], ["modelo", "Do modelo"], ["documento", "De documento"]].map(([v, l]) => chip(l, all.source === v, pick("source", v)))),
    );
    clear(list);
    if (!data.items.length) list.append(empty("Nenhuma nota com esses filtros."));
    for (const n of data.items) list.append(noteItem(n, true));
    clear(pager).append(
      h("button", { type: "button", class: "ghost", disabled: all.offset === 0, onclick: async () => { all.offset = Math.max(0, all.offset - PAGE); await draw(); }, text: "‹ Novas" }),
      h("span", { class: "muted small", text: `${data.total ? all.offset + 1 : 0}–${Math.min(all.offset + PAGE, data.total)} de ${data.total}` }),
      h("button", { type: "button", class: "ghost", disabled: all.offset + PAGE >= data.total, onclick: async () => { all.offset += PAGE; await draw(); }, text: "Antigas ›" }),
    );
  }
  let timer;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(async () => { all.q = search.value.trim(); all.offset = 0; await draw(); }, 250); });
  await draw();
  return h("div", {},
    h("p", {}, h("a", { href: "#/estudos", text: "‹ Todos os temas" })),
    h("h1", { text: "Todas as notas" }), card("", search, filters, list, pager));
}

export async function render(rest) {
  if (rest[0] === "notas") return everyNote();
  if (rest[0]) return topic(rest[0]);
  const data = await api("/interests", { limit: 200 });
  const list = h("div", { class: "grid" });
  if (!data.items.length) list.append(empty("Ela ainda não estuda nenhum tema. Os temas nascem das conversas, ou de `juniper study add`."));
  for (const t of data.items) {
    list.append(
      h("a", { class: "card topic", href: `#/estudos/${t.id}` },
        h("h2", { text: t.topic }),
        h("p", {}, badge(`passo ${t.level}`), " ", badge(t.stance_words, t.stance === "dislike" ? "warn" : "")),
        h("div", { class: "row" }, h("span", { class: "label", text: "Intensidade" }), bar(t.intensity), h("span", { class: "muted small", text: t.intensity.toFixed(2) })),
        h("p", { class: "muted small", text: `${t.notes} nota(s) · ${t.questions} pergunta(s) em aberto · ${t.last_studied_at ? `estudado ${when(t.last_studied_at)}` : "ainda não estudado"}` }),
      ),
    );
  }
  return h("div", {}, h("h1", { text: "Estudos" }),
    h("p", {}, h("a", { href: "#/estudos/notas", text: "Ver todas as notas, de todos os temas ›" })), list);
}
