import { api } from "/assets/api.js";
import { lineChart } from "/assets/charts.js";
import { badge, bar, card, clear, empty, h, signedBar, when } from "/assets/ui.js";

const state = { origin: "", kind: "", days: 14, offset: 0 };

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function traceNode(trace) {
  return h("div", { class: "trace" }, ...trace.map((t) => {
    if (t.kind === "turn") {
      if (t.hidden) return h("p", { class: "muted", text: "A fala está oculta (dashboard.transcripts desligado)." });
      if (t.missing) return h("p", { class: "muted", text: "A fala de origem não está mais guardada." });
      return h("div", {},
        h("p", { class: "label", text: `O que o Petrus escreveu · ${when(t.at)}` }),
        h("blockquote", { text: t.user }),
        t.assistant ? h("p", { class: "label", text: "O que ela respondeu" }) : null,
        t.assistant ? h("blockquote", { class: "her", text: t.assistant }) : null,
      );
    }
    if (t.kind === "concern") {
      return t.missing
        ? h("p", { class: "muted", text: `Preocupação ${t.id} (não existe mais)` })
        : h("p", {}, h("span", { class: "label", text: "Atingiu o que importa para ela: " }), `${t.name} (peso ${t.weight.toFixed(2)})`);
    }
    if (t.kind === "belief") {
      return h("p", {}, h("span", { class: "label", text: "Ligado à crença: " }), t.statement || `${t.id} (apagada)`);
    }
    return h("p", { class: "muted", text: t.ref });
  }));
}

async function showDetail(panel, id) {
  clear(panel).append(h("p", { class: "muted", text: "Carregando…" }));
  const a = await api(`/affects/${id}`);
  clear(panel).append(
    h("h3", { text: `${a.grievance ? "Mágoa" : a.kind} ` }, badge(a.origin, a.origin === "conversa" ? "live" : "")),
    h("p", { class: "muted small", text: `${when(a.occurred_at)} · valência ${a.valence.toFixed(2)} · registrada ${a.intensity.toFixed(2)} · agora ${a.strength.toFixed(2)}${a.resolved ? " · desfeita" : ""} · sobre: ${a.about}` }),
    h("p", { class: "label", text: "De onde veio" }),
    traceNode(a.trace),
  );
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

export async function render() {
  const root = h("div", {});
  const chartBox = h("div", {});
  const list = h("div", {});
  const panel = h("section", { class: "card detail", "aria-live": "polite" }, empty("Escolha um sentimento para ver de onde veio."));
  const filters = h("div", { class: "chips" });
  const pager = h("div", { class: "pager" });

  async function drawChart() {
    const mood = await api("/mood", { days: state.days });
    clear(chartBox).append(
      lineChart(mood.points.map((p) => ({ at: p.at, value: p.mood })), { label: "Humor ao longo do tempo" }),
      h("div", { class: "chips" }, ...[7, 14, 30].map((d) => chip(`${d} dias`, state.days === d, async () => { state.days = d; await drawChart(); }))),
    );
  }

  async function draw() {
    const data = await api("/affects", { origin: state.origin, kind: state.kind, limit: 20, offset: state.offset });
    clear(filters).append(
      ...[["", "Todos"], ["conversa", "Na conversa"], ["sonho", "No sonho"]].map(([v, l]) =>
        chip(l, state.origin === v, async () => { state.origin = v; state.offset = 0; await draw(); })),
    );
    clear(list);
    if (!data.items.length) list.append(empty("Nenhum sentimento registrado."));
    else {
      list.append(
        h("div", { class: "row-head", "aria-hidden": "true" },
          h("span", { text: "Quando" }), h("span", { text: "Sentimento" }), h("span", { text: "Origem" }),
          h("span", { text: "Valência (– / +)" }), h("span", { text: "Força" }), h("span", { text: "Agora" }),
        ),
      );
    }
    for (const a of data.items) {
      list.append(
        h("button", { type: "button", class: "row-item", onclick: () => showDetail(panel, a.id) },
          h("span", { class: "when", text: when(a.occurred_at) }),
          h("span", { class: "kind", text: a.grievance ? "mágoa" : a.kind }),
          badge(a.origin, a.origin === "conversa" ? "live" : ""),
          signedBar(a.valence),
          bar(a.strength, a.valence < 0 ? "neg" : "pos"),
          h("span", { class: "muted small", text: a.resolved ? "desfeita" : a.strength.toFixed(2) }),
        ),
      );
    }
    clear(pager).append(
      h("button", { type: "button", class: "ghost", disabled: state.offset === 0, onclick: async () => { state.offset = Math.max(0, state.offset - 20); await draw(); }, text: "‹ Novos" }),
      h("span", { class: "muted small", text: `${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + 20, data.total)} de ${data.total}` }),
      h("button", { type: "button", class: "ghost", disabled: state.offset + 20 >= data.total, onclick: async () => { state.offset += 20; await draw(); }, text: "Antigos ›" }),
    );
  }

  await Promise.all([drawChart(), draw()]);
  root.append(
    h("h1", { text: "Afetos" }),
    card("Humor", chartBox),
    card("Sentimentos", filters, list, pager),
    panel,
  );
  return root;
}
