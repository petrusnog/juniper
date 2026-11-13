import { api } from "/assets/api.js";
import { barChart } from "/assets/charts.js";
import { badge, card, clear, empty, h } from "/assets/ui.js";

// Auditoria: everything Juniper did, in sentences, searchable. Every time shown comes from the
// server already written in the agent's time zone (UTC-3), never from the browser's.
const PERIODS = [["1h", "última hora"], ["today", "hoje"], ["yesterday", "ontem"], ["7d", "7 dias"], ["30d", "30 dias"], ["", "tudo"]];
const FACETS = [
  ["category", "Tipo de ação"],
  ["outcome", "Desfecho"],
  ["model", "Modelo"],
  ["provider", "Provedor"],
  ["channel", "Canal"],
  ["severity", "Severidade"],
  ["actor", "Quem"],
];
const OUTCOME = { ok: ["ok", ""], failed: ["falhou", "warn"], pending: ["pendente", "warn"], denied: ["negado", "warn"], partial: ["parcial", "live"] };
const MULTI = [...FACETS.map(([name]) => name), "action"];
const SIZE = 50;

// --- the filters live in the address, so a search can be bookmarked and shared -----------------

function readState() {
  const query = new URLSearchParams(window.location.hash.split("?")[1] || "");
  const state = { q: query.get("q") || "", period: query.has("period") ? query.get("period") : "today", from: query.get("from") || "", to: query.get("to") || "", session: query.get("session") || "", turn: query.get("turn") || "", lang: query.get("lang") || "", offset: Number(query.get("offset")) || 0 };
  for (const name of MULTI) state[name] = query.getAll(name).flatMap((v) => v.split(",")).filter(Boolean);
  return state;
}

function params(state, extra = {}) {
  const out = { q: state.q, session: state.session, turn: state.turn, lang: state.lang, ...extra };
  if (state.from || state.to) {
    out.from = state.from;
    out.to = state.to;
  } else if (state.period) out.period = state.period;
  for (const name of MULTI) if (state[name].length) out[name] = state[name];
  return out;
}

function writeHash(state) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params(state, { offset: state.offset || "" }))) {
    if (Array.isArray(value)) for (const item of value) query.append(key, item);
    else if (value !== "" && value !== undefined) query.set(key, String(value));
  }
  if (!state.period && !state.from && !state.to) query.set("period", "");
  const suffix = query.toString() ? `?${query}` : "";
  window.history.replaceState(null, "", `#/auditoria${suffix}`);
}

const active = (state) => state.q || state.session || state.turn || state.from || state.to || state.period !== "today" || MULTI.some((n) => state[n].length);

// --- small pieces ------------------------------------------------------------------------------

function chip(label, on, onclick, count) {
  return h("button", { type: "button", class: `chip ${on ? "on" : ""}`.trim(), "aria-pressed": on ? "true" : "false", onclick },
    label, count !== undefined ? h("span", { class: "n", text: String(count) }) : null);
}

function outcomeBadge(outcome) {
  const [text, kind] = OUTCOME[outcome] || [outcome, ""];
  return badge(text, kind);
}

function row(item, { grouped = false } = {}) {
  const meta = [item.category, item.model || item.provider, item.channel && `${item.channel}${item.voice ? " · voz" : ""}`, item.duration_ms != null ? `${item.duration_ms} ms` : null].filter(Boolean);
  return h("a", { class: `audit-row ${item.outcome}`, href: `#/auditoria/${encodeURIComponent(item.id)}` },
    h("span", { class: "clock", text: item.clock }),
    h("span", { class: "said", text: item.summary }),
    h("span", { class: "meta" }, outcomeBadge(item.outcome), item.severity === "error" ? badge("erro", "warn") : null, meta.join(" · ")));
}

// Consecutive records of the same message (turn) fold into one expandable line.
function timeline(items) {
  const nodes = [];
  for (let i = 0; i < items.length;) {
    const turn = items[i].turn;
    let j = i + 1;
    while (turn && j < items.length && items[j].turn === turn) j += 1;
    if (j - i > 1) {
      const group = items.slice(i, j);
      const failed = group.filter((g) => g.outcome === "failed").length;
      const summary = `Mensagem das ${group.at(-1).clock.slice(11, 19)}: ${group.length} passos${failed ? `, ${failed} com falha` : ""}`;
      nodes.push(h("details", { class: "audit-group" }, h("summary", { text: summary }), ...group.map((g) => row(g, { grouped: true }))));
    } else nodes.push(row(items[i]));
    i = j;
  }
  return nodes;
}

// "2026-11-13T14:00" + 1 h without going through the browser's time zone.
function plusHour(label) {
  const [d, t] = label.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh] = t.split(":").map(Number);
  const moment = new Date(Date.UTC(y, m - 1, day, hh + 1));
  const pad = (n) => String(n).padStart(2, "0");
  return `${moment.getUTCFullYear()}-${pad(moment.getUTCMonth() + 1)}-${pad(moment.getUTCDate())}T${pad(moment.getUTCHours())}:00`;
}

// --- the list ---------------------------------------------------------------------------------

async function list() {
  const state = readState();
  const holder = h("div", {});
  const summaryHolder = h("div", {});
  const body = h("div", {});

  async function refresh(resetOffset = true) {
    if (resetOffset) state.offset = 0;
    writeHash(state);
    await Promise.all([drawList(), drawSummary()]);
  }

  function toggle(name, value) {
    const values = state[name];
    state[name] = values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
    return refresh();
  }

  async function drawSummary() {
    const data = await api("/activity/summary", params(state));
    const stats = h("div", { class: "stats" },
      ...[["registros", data.total], ["falhas", data.failed], ["pendentes", data.pending], ["negadas", data.denied], ["trocas de provedor", data.failovers]].map(([label, value]) =>
        h("div", { class: "stat" }, h("span", { class: "num", text: String(value) }), h("span", { class: "muted small", text: label }))));
    const buckets = data.timeline.map((b) => ({
      label: data.by === "hour" ? `${b.at.slice(8, 10)}/${b.at.slice(5, 7)} ${b.at.slice(11, 13)}h` : `${b.at.slice(8, 10)}/${b.at.slice(5, 7)}`,
      count: b.count, failed: b.failed, at: b.at,
    }));
    const chart = buckets.length ? barChart(buckets, {
      label: `Atividade por ${data.by === "hour" ? "hora" : "dia"} (${data.utc})`,
      onPick: (bucket) => {
        state.period = "";
        state.from = bucket.at;
        state.to = data.by === "hour" ? plusHour(bucket.at) : bucket.at;
        refresh();
      },
    }) : empty("Sem atividade neste período.");
    const models = data.models.length ? h("table", { class: "rank" },
      h("thead", {}, h("tr", {}, ...["Modelo", "Chamadas", "Tempo médio", "Tokens"].map((t) => h("th", { text: t })))),
      h("tbody", {}, ...data.models.map((m) => h("tr", {}, h("td", { text: m.model }), h("td", { text: String(m.calls) }), h("td", { text: `${(m.avg_ms / 1000).toFixed(1)} s` }), h("td", { text: String(m.tokens) }))))) : null;
    const actions = data.actions.length ? h("p", { class: "small" }, ...data.actions.map(([name, n]) => chip(`${name}`, state.action.includes(name), () => toggle("action", name), n))) : null;
    clear(summaryHolder).append(card("Resumo do período",
      stats,
      h("p", { class: "muted small", text: `Atividade por ${data.by === "hour" ? "hora" : "dia"} (horário ${data.utc}). Clique numa barra para filtrar.${data.capped ? " Resumo dos 20.000 registros mais recentes." : ""}` }),
      chart, models ? h("h3", { text: "Modelos" }) : null, models, actions ? h("h3", { text: "Ações mais comuns" }) : null, actions));
  }

  async function drawList() {
    const data = await api("/activity", { ...params(state), limit: SIZE, offset: state.offset });
    const language = state.lang || data.lang;
    const filters = h("div", {});
    for (const [name, title] of FACETS) {
      const options = data.facets?.[name] || [];
      const selected = state[name].filter((v) => !options.some(([value]) => value === v));
      if (!options.length && !selected.length) continue;
      filters.append(h("div", { class: "facet" }, h("span", { class: "label", text: title }),
        h("div", { class: "chips", role: "group", "aria-label": title },
          ...selected.map((v) => chip(v, true, () => toggle(name, v), 0)),
          ...options.map(([value, n]) => chip(OUTCOME[value] && name === "outcome" ? OUTCOME[value][0] : value, state[name].includes(value), () => toggle(name, value), n)))));
    }
    const page = h("div", { class: "pager" },
      h("button", { type: "button", class: "ghost", disabled: state.offset === 0, onclick: () => { state.offset = Math.max(0, state.offset - SIZE); refresh(false); }, text: "‹ Mais novos" }),
      h("span", { class: "muted small", text: `${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + SIZE, data.total)} de ${data.total}` }),
      h("button", { type: "button", class: "ghost", disabled: state.offset + SIZE >= data.total, onclick: () => { state.offset += SIZE; refresh(false); }, text: "Mais antigos ›" }));
    const exportQuery = new URLSearchParams();
    for (const [key, value] of Object.entries(params(state))) {
      if (Array.isArray(value)) for (const item of value) exportQuery.append(key, item);
      else if (value !== "" && value !== undefined) exportQuery.set(key, String(value));
    }
    const pinned = [state.turn && `mensagem ${state.turn}`, state.session && `sessão ${state.session}`].filter(Boolean);
    clear(body).append(
      card("Filtros", filters,
        pinned.length ? h("p", { class: "small" }, `Limitado a: ${pinned.join(", ")} `, h("button", { type: "button", class: "ghost", text: "tirar", onclick: () => { state.turn = ""; state.session = ""; refresh(); } })) : null,
        h("p", {}, h("button", { type: "button", class: "ghost", text: "Limpar filtros", onclick: () => { Object.assign(state, readEmpty()); const box = document.getElementById("audit-q"); if (box) box.value = ""; refresh(); } }), " ",
          h("a", { class: "button", href: `/api/activity/export.xlsx?${exportQuery}`, download: "", text: "Baixar planilha (.xlsx)" }), " ",
          h("select", { "aria-label": "Idioma dos textos", onchange: (event) => { state.lang = event.target.value; refresh(false); } },
            ...data.languages.map((l) => h("option", { value: l, selected: l === language ? "" : null, text: l }))))),
      card(`Linha do tempo (${data.total})`,
        data.items.length ? h("div", {}, ...timeline(data.items)) : empty(active(state) ? "Nada com esses filtros." : "Nada registrado ainda."),
        page),
    );
  }

  const readEmpty = () => ({ q: "", period: "today", from: "", to: "", session: "", turn: "", offset: 0, ...Object.fromEntries(MULTI.map((n) => [n, []])) });

  const search = h("input", { id: "audit-q", type: "search", placeholder: "buscar no que ela fez e no log técnico", value: state.q, "aria-label": "Buscar", maxlength: 200 });
  const from = h("input", { type: "datetime-local", value: state.from.slice(0, 16), "aria-label": "De" });
  const to = h("input", { type: "datetime-local", value: state.to.slice(0, 16), "aria-label": "Até" });
  const form = h("form", { class: "audit-search" }, search, h("button", { type: "submit", text: "Buscar" }));
  form.addEventListener("submit", (event) => { event.preventDefault(); state.q = search.value.trim(); refresh(); });
  const periods = h("div", { class: "chips", role: "group", "aria-label": "Período" },
    ...PERIODS.map(([id, label]) => chip(label, !state.from && !state.to && state.period === id, () => { state.period = id; state.from = ""; state.to = ""; from.value = ""; to.value = ""; refresh().then(() => redrawPeriods()); })));
  function redrawPeriods() { periods.replaceChildren(...PERIODS.map(([id, label]) => chip(label, !state.from && !state.to && state.period === id, () => { state.period = id; state.from = ""; state.to = ""; from.value = ""; to.value = ""; refresh().then(() => redrawPeriods()); }))); }
  const range = h("div", { class: "audit-range" },
    h("label", {}, "De", from), h("label", {}, "Até", to),
    h("button", { type: "button", class: "ghost", text: "Aplicar intervalo", onclick: () => { state.period = ""; state.from = from.value; state.to = to.value; refresh().then(() => redrawPeriods()); } }));

  await refresh(false);
  clear(holder).append(card("", form, periods, range), summaryHolder, body);
  return h("div", {},
    h("h1", { text: "Auditoria" }),
    h("p", { class: "muted", text: "Tudo o que a Juniper fez, em frases, com data e hora em UTC-3. Cada registro guarda também o log técnico." }),
    holder);
}

// --- one record --------------------------------------------------------------------------------

async function detail(id) {
  const lang = new URLSearchParams(window.location.hash.split("?")[1] || "").get("lang") || "";
  const o = await api(`/activity/${encodeURIComponent(id)}`, { lang });
  const facts = [
    ["Quando", o.clock], ["Registro", o.id], ["Ação", `${o.category} · ${o.action}`], ["Quem", o.actor],
    ["Canal", o.channel ? `${o.channel}${o.voice ? " · voz" : ""}` : null],
    ["Provedor e modelo", o.provider || o.model ? `${o.provider || "?"}/${o.model || "?"}` : null],
    ["Ferramenta", o.tool ? `${o.tool}${o.tier ? ` (${o.tier})` : ""}` : null],
    ["Decisão", o.decision], ["Alvo", o.target], ["Duração", o.duration_ms != null ? `${o.duration_ms} ms` : null],
    ["Tokens", o.tokens], ["Erro", o.error_type], ["Idioma do texto", o.locale], ["Versão", o.version ? `${o.version} · ${String(o.commit || "").slice(0, 8)}` : null],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");
  const list = h("ul", { class: "plain" }, ...facts.map(([label, value]) => h("li", {}, h("span", { class: "label", text: `${label} ` }), String(value))));
  const technical = JSON.stringify(o.raw || {}, null, 2);
  const copy = h("button", { type: "button", class: "ghost", text: "Copiar", onclick: async (event) => { try { await navigator.clipboard.writeText(technical); event.target.textContent = "Copiado"; } catch { event.target.textContent = "Não consegui copiar"; } } });
  const links = h("p", {},
    o.turn ? h("a", { class: "button", href: `#/auditoria?turn=${encodeURIComponent(o.turn)}&period=`, text: "Ver tudo desta mensagem" }) : null, " ",
    o.session ? h("a", { class: "button", href: `#/auditoria?session=${encodeURIComponent(o.session)}&period=`, text: "Ver a sessão no log" }) : null, " ",
    o.session ? h("a", { class: "button", href: `#/conversas/${encodeURIComponent(o.session)}`, text: "Abrir a conversa" }) : null, " ",
    h("a", { class: "button", href: `/api/activity/export.xlsx?${new URLSearchParams({ ...(o.turn ? { turn: o.turn } : { session: o.session || "" }), period: "" })}`, download: "", text: "Baixar (.xlsx)" }));
  const sections = [
    card("", h("p", {}, outcomeBadge(o.outcome)), h("p", { class: "said", text: o.summary }), list, links),
    card("Técnico (o evento do log, como foi gravado)", h("p", {}, copy), h("pre", { class: "block", text: technical })),
  ];
  if (o.steps?.length > 1) sections.push(card("Passos desta mensagem", ...o.steps.map((s) => row(s))));
  return h("div", {}, h("p", {}, h("a", { href: "#/auditoria", text: "‹ Auditoria" })), h("h1", { text: "Registro" }), ...sections);
}

export async function render(rest) {
  try {
    if (rest[0]) return await detail(rest[0]);
    return await list();
  } catch (error) {
    if (error.status === 503) return h("div", {}, h("h1", { text: "Auditoria" }), card("", empty(error.message)));
    throw error;
  }
}
