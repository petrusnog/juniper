import { api } from "/assets/api.js";
import { dangerAction } from "/assets/actions.js";
import { badge, bar, card, clear, empty, h, when } from "/assets/ui.js";

const PAGE = 20;
const state = { status: "active", reflexao: "", type: "", q: "", offset: 0 };

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function reflexaoBadge(m) {
  return badge(m.status_reflexao, m.status_reflexao === "refinada" ? "live" : "");
}

function link(id, text) {
  return h("a", { href: `#/memorias/${id}`, text });
}

function lembranca(l) {
  const rows = [];
  if (l.contexto) rows.push(h("p", {}, h("span", { class: "label", text: "Contexto: " }), l.contexto));
  if (l.sentimento?.length) {
    rows.push(h("p", {}, h("span", { class: "label", text: "Na época ela sentiu: " }),
      ...l.sentimento.map((s) => badge(`${s.kind} (${Number(s.intensity).toFixed(2)})`, s.valence < 0 ? "warn" : ""))));
  }
  if (l.como_eu_era?.jeito) {
    rows.push(h("p", {}, h("span", { class: "label", text: "Como ela era: " }), `${l.como_eu_era.jeito} (personalidade v${l.como_eu_era.versao})`));
  }
  if (l.relevancia != null) {
    rows.push(h("div", { class: "row" }, h("span", { class: "label", text: "Relevância" }), bar(l.relevancia), h("span", { class: "muted small", text: l.relevancia.toFixed(2) })));
  }
  if (l.refinada_em) rows.push(h("p", { class: "muted small", text: `Refinada em ${when(l.refinada_em)}` }));
  return rows;
}

async function detail(id) {
  const m = await api(`/memories/${encodeURIComponent(id)}`);
  const linked = [];
  for (const other of m.linked?.memorias || []) {
    if (other) linked.push(h("li", {}, link(other.id, other.text), " ", badge(other.status_reflexao)));
  }
  for (const b of m.linked?.crencas || []) linked.push(h("li", {}, h("span", { class: "label", text: "crença: " }), b.statement));
  return h("div", {},
    h("p", {}, h("a", { href: "#/memorias", text: "‹ Voltar à lista" })),
    card("Memória",
      h("p", { class: "lead", text: m.text }),
      h("p", {}, badge(m.type), " ", reflexaoBadge(m), " ", badge(m.status, m.status === "superseded" ? "warn" : "")),
      h("p", { class: "muted small", text: `${m.id} · criada ${when(m.created_at)} · origem: conversa ${m.session_id || "—"}, turno ${m.turn ?? "—"}` }),
    ),
    m.lembranca ? card("Lembrança (refinada pelo sonho)", ...lembranca(m.lembranca)) : null,
    m.from || m.into
      ? card("Linhagem",
          m.from ? h("p", {}, h("span", { class: "label", text: "Veio da bruta: " }), link(m.from.id, m.from.text), ` (${m.from.status})`) : null,
          m.into ? h("p", {}, h("span", { class: "label", text: "Virou a refinada: " }), link(m.into.id, m.into.text)) : null,
        )
      : null,
    linked.length ? card("Ligações", h("ul", { class: "plain" }, ...linked)) : null,
    card("Ações", h("p", { class: "muted small", text: "Esquecer apaga esta memória para sempre, junto com as versões antigas que ela substituiu." }),
      dangerAction("Esquecer esta memória", m.id, `/memory/facts/${m.id}/forget`, () => { window.location.hash = "#/memorias"; })),
  );
}

export async function render(rest) {
  if (rest[0]) return detail(rest[0]);

  const filters = h("div", {});
  const list = h("div", {});
  const pager = h("div", { class: "pager" });
  const search = h("input", { type: "search", placeholder: "Buscar no texto…", "aria-label": "Buscar memórias", value: state.q });

  async function draw() {
    const data = await api("/memories", { status: state.status, reflexao: state.reflexao, type: state.type, q: state.q, limit: PAGE, offset: state.offset });
    const choose = (key, value) => async () => { state[key] = value; state.offset = 0; await draw(); };
    clear(filters).append(
      h("div", { class: "chips", role: "group", "aria-label": "Situação" },
        ...[["active", "Ativas"], ["superseded", "Substituídas"], ["all", "Todas"]].map(([v, l]) => chip(l, state.status === v, choose("status", v)))),
      h("div", { class: "chips", role: "group", "aria-label": "Reflexão" },
        ...[["", "Brutas e refinadas"], ["bruta", "Brutas"], ["refinada", "Refinadas"]].map(([v, l]) => chip(l, state.reflexao === v, choose("reflexao", v)))),
      h("div", { class: "chips", role: "group", "aria-label": "Tipo" },
        ...[["", "Todos os tipos"], ["fact", "fact"], ["preference", "preference"], ["project", "project"]].map(([v, l]) => chip(l, state.type === v, choose("type", v)))),
    );
    clear(list);
    if (!data.items.length) list.append(empty("Nenhuma memória com esses filtros."));
    for (const m of data.items) {
      list.append(
        h("a", { class: "mem-item", href: `#/memorias/${m.id}` },
          h("span", { class: "mem-text", text: m.text }),
          h("span", { class: "mem-meta" }, badge(m.type), reflexaoBadge(m), m.status === "superseded" ? badge("substituída", "warn") : null, h("span", { class: "muted small", text: when(m.created_at) })),
          m.lembranca?.contexto ? h("span", { class: "muted small mem-context", text: m.lembranca.contexto }) : null,
        ),
      );
    }
    clear(pager).append(
      h("button", { type: "button", class: "ghost", disabled: state.offset === 0, onclick: async () => { state.offset = Math.max(0, state.offset - PAGE); await draw(); }, text: "‹ Novas" }),
      h("span", { class: "muted small", text: `${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + PAGE, data.total)} de ${data.total}` }),
      h("button", { type: "button", class: "ghost", disabled: state.offset + PAGE >= data.total, onclick: async () => { state.offset += PAGE; await draw(); }, text: "Antigas ›" }),
    );
  }

  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => { state.q = search.value.trim(); state.offset = 0; await draw(); }, 250);
  });
  await draw();
  return h("div", {}, h("h1", { text: "Memórias" }), card("", search, filters, list, pager));
}
