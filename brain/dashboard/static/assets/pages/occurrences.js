import { api, post } from "/assets/api.js";
import { ago, badge, card, clear, empty, h, when } from "/assets/ui.js";

const SIZE = 25;
const FILTERS = [
  { id: "todos", label: "Todas" },
  { id: "relato", label: "Marcadas por mim" },
  { id: "automatico", label: "Detectadas" },
  { id: "registro", label: "Registradas" },
];
const TYPES = [["bug", "bug"], ["vazamento", "vazamento"], ["comportamento", "comportamento"], ["ideia", "ideia"], ["outro", "outro"]];
const SEVERITIES = [["baixa", "baixa"], ["media", "média"], ["alta", "alta"]];
const STATES = [["aberta", "aberta"], ["investigando", "investigando"], ["resolvida", "resolvida"], ["descartada", "descartada"]];
const STATE_KIND = { aberta: "warn", investigando: "live", resolvida: "", descartada: "" };
const state = { kind: "todos", offset: 0 };

function exportUrl(params = {}) {
  const query = new URLSearchParams(params);
  const suffix = query.toString() ? `?${query}` : "";
  return `/api/occurrences/export.xlsx${suffix}`;
}

function download(label, params) {
  // A plain link: the browser sends the session cookie and saves the file.
  return h("a", { class: "button", href: exportUrl(params), download: "", text: label });
}

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function kindBadge(item) {
  if (item.kind === "registro") return badge(`registro · ${item.state}`, STATE_KIND[item.state] || "");
  return item.kind === "relato" ? badge("marcada por mim", "live") : badge("detectada", item.quarantined ? "warn" : "");
}

function where(item) {
  const parts = [item.channel || "?"];
  if (item.voice) parts.push("voz");
  return parts.join(" · ");
}

function row(item) {
  const what = item.kind === "registro"
    ? `${item.title} (${item.type}, gravidade ${item.severity})`
    : item.kind === "relato" ? (item.note || "(sem nota)") : (item.rules || []).join(", ") || "(sem regra)";
  return h("a", { class: "mem-item", href: `#/ocorrencias/${encodeURIComponent(item.id)}` },
    h("span", { class: "mem-meta" },
      kindBadge(item),
      h("span", { class: "muted small", text: `${when(item.at)} (${ago(item.at)}) · ${where(item)}` }),
    ),
    h("span", { class: "mem-text", text: what }),
    item.preview ? h("span", { class: "muted small mem-context", text: item.preview }) : null,
  );
}

function select(label, options, value) {
  const node = h("select", { "aria-label": label }, ...options.map(([v, l]) => h("option", { value: v, text: l })));
  node.value = value;
  return node;
}

function newIssueForm(refresh) {
  const title = h("input", { type: "text", required: true, maxlength: 120, "aria-label": "Título" });
  const description = h("input", { type: "text", maxlength: 4000, "aria-label": "O que aconteceu" });
  const type = select("Tipo", TYPES, "bug");
  const severity = select("Gravidade", SEVERITIES, "media");
  const error = h("p", { class: "error small", role: "alert", hidden: true });
  const form = h("form", {}, h("label", { text: "Título" }), title, h("label", { text: "O que aconteceu" }), description,
    h("label", { text: "Tipo" }), type, h("label", { text: "Gravidade" }), severity,
    h("button", { type: "submit", text: "Registrar" }), error);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    try {
      await post("/issues", { title: title.value, description: description.value, type: type.value, severity: severity.value });
      await refresh();
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  });
  return card("Registrar uma ocorrência", h("p", { class: "muted small", text: "Qualquer bug ou comportamento errado. Pela conversa, diga «anota isso como bug»." }), form);
}

async function list() {
  const holder = h("div", {});

  async function draw() {
    const data = await api("/occurrences", { kind: state.kind, limit: SIZE, offset: state.offset });
    const box = h("div", {});
    if (!data.items.length) {
      box.append(empty("Nenhuma ocorrência ainda. Registre uma aqui, peça à Juniper («anota isso como bug») ou, para vazamentos, escreva /vazou."));
    }
    for (const item of data.items) box.append(row(item));
    const go = async (offset) => { state.offset = Math.max(0, offset); await draw(); };
    const pager = h("div", { class: "pager" },
      h("button", { type: "button", class: "ghost", disabled: state.offset === 0, onclick: () => go(state.offset - SIZE), text: "‹ Anteriores" }),
      h("span", { class: "muted small", text: `${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + SIZE, data.total)} de ${data.total}` }),
      h("button", { type: "button", class: "ghost", disabled: state.offset + SIZE >= data.total, onclick: () => go(state.offset + SIZE), text: "Seguintes ›" }),
    );
    const chips = h("div", { class: "chips" },
      ...FILTERS.map((f) => chip(f.label, state.kind === f.id, async () => { state.kind = f.id; await go(0); })));
    clear(holder).append(newIssueForm(draw), card("", chips, h("p", {}, download("Baixar planilha (.xlsx)", { kind: state.kind })), box, pager));
  }

  await draw();
  return h("div", {},
    h("h1", { text: "Ocorrências" }),
    h("p", { class: "muted", text: "Bugs e comportamentos errados que você registrou (aqui ou por fala), vazamentos que você marcou (/vazou, «isso vazou») e os que a auditoria detectou." }),
    holder,
  );
}

function bubble(message) {
  const mine = message.author === "Usuário";
  const raw = message.author.includes("bruto");
  return h("div", { class: "turn" },
    h("p", { class: "muted small", text: [message.author, message.note, message.at ? when(message.at) : ""].filter(Boolean).join(" · ") }),
    h("p", { class: `bubble ${mine ? "me" : "her"}${raw ? " raw" : ""}`, text: message.text || "(vazio)" }),
  );
}

function issueControls(o) {
  const reload = async () => { window.dispatchEvent(new HashChangeEvent("hashchange")); };
  const act = async (path, params, node) => {
    try {
      await post(`/issues/${o.id}/${path}`, params);
      await reload();
    } catch (e) {
      node.textContent = e.message;
      node.hidden = false;
    }
  };
  const error = h("p", { class: "error small", role: "alert", hidden: true });
  const state = select("Estado", STATES, o.state);
  const severity = select("Gravidade", SEVERITIES, o.severity);
  const note = h("input", { type: "text", maxlength: 1000, "aria-label": "Nota" });
  const confirm = h("input", { type: "text", placeholder: o.id, "aria-label": `Digite ${o.id} para apagar`, autocomplete: "off" });
  return card("Acompanhar",
    h("label", { text: "Estado" }), state,
    h("button", { type: "button", class: "ghost", text: "Mudar estado", onclick: () => act("state", { value: state.value }, error) }),
    h("label", { text: "Gravidade" }), severity,
    h("button", { type: "button", class: "ghost", text: "Mudar gravidade", onclick: () => act("severity", { value: severity.value }, error) }),
    h("label", { text: "Nota" }), note,
    h("button", { type: "button", class: "ghost", text: "Adicionar nota", onclick: () => act("note", { text: note.value }, error) }),
    h("label", { text: `Para apagar, digite «${o.id}»` }), confirm,
    h("button", { type: "button", class: "ghost", text: "Apagar ocorrência", onclick: async () => {
      try { await post(`/issues/${o.id}/delete`, { confirm: confirm.value.trim() }); window.location.hash = "#/ocorrencias"; }
      catch (e) { error.textContent = e.message; error.hidden = false; }
    } }),
    error);
}

async function detail(id) {
  const o = await api(`/occurrences/${encodeURIComponent(id)}`);
  const facts = h("ul", { class: "plain" },
    h("li", {}, h("span", { class: "label", text: "Sessão " }), h("a", { href: `#/conversas/${encodeURIComponent(o.session)}`, text: o.session })),
    h("li", {}, h("span", { class: "label", text: "Quando " }), `${when(o.at)} (${ago(o.at)})`),
    h("li", {}, h("span", { class: "label", text: "Canal " }), where(o)),
  );
  if (o.kind === "registro") {
    facts.append(h("li", {}, h("span", { class: "label", text: "Tipo " }), `${o.type} · gravidade ${o.severity} · origem ${o.origin}`));
    facts.append(h("li", {}, h("span", { class: "label", text: "Título " }), o.title));
  } else if (o.note) facts.append(h("li", {}, h("span", { class: "label", text: "Nota " }), o.note));
  if (o.kind === "automatico") {
    facts.append(h("li", {}, h("span", { class: "label", text: "Modelo " }), `${o.provider || "?"}/${o.model || "?"} · ${o.finish_reason || "?"}`));
    facts.append(h("li", {}, h("span", { class: "label", text: "Chegou a você " }),
      o.quarantined ? "sim, e o turno ficou fora da memória" : o.leaked ? "sim (o turno seguiu para a memória)" : "não: o guarda cortou"));
  }
  if (o.audit?.length) facts.append(h("li", {}, h("span", { class: "label", text: "Registros automáticos da sessão " }), o.audit.join(", ")));
  if (o.commit) facts.append(h("li", {}, h("span", { class: "label", text: "Versão " }), `${o.version || "?"} · ${String(o.commit).slice(0, 8)}`));

  const sections = [
    card("", h("p", {}, kindBadge(o)), facts, h("p", {}, download("Baixar esta ocorrência (.xlsx)", { id: o.id }))),
    ...(o.kind === "registro" ? [issueControls(o)] : []),
    card(o.kind === "registro" ? "Descrição e histórico" : "Mensagens", ...o.messages_list.map(bubble)),
  ];
  if (o.hits?.length) {
    const hits = h("ul", { class: "plain" });
    for (const hit of o.hits) {
      hits.append(h("li", {}, badge(hit.rule), h("span", { class: "muted small", text: `${hit.where === "delivered" ? "entregue" : "bruto"} · posição ${hit.at}` }), h("pre", { class: "block", text: hit.excerpt })));
    }
    sections.push(card("O que disparou", hits));
  }
  return h("div", {}, h("p", {}, h("a", { href: "#/ocorrencias", text: "‹ Todas as ocorrências" })), h("h1", { text: "Ocorrência" }), h("p", { class: "muted small", text: o.id }), ...sections);
}

export async function render(rest) {
  try {
    if (rest[0]) return await detail(rest[0]);
    return await list();
  } catch (error) {
    if (error.status === 503) {
      return h("div", {}, h("h1", { text: "Ocorrências" }), card("", empty(error.message)));
    }
    throw error;
  }
}
