// How she changes (ADR-030): the voice now, the mood in PAD, each aspect over time with the
// conversations that moved it, and the confidence curve of each belief by layer.
import { api } from "/assets/api.js";
import { lineChart } from "/assets/charts.js";
import { badge, bar, card, clear, empty, h, when } from "/assets/ui.js";

const state = { days: 14, trait: "", layer: "user", belief: "" };
const LAYERS = [["user", "Sobre o Petrus"], ["self", "Sobre mim"], ["world", "Sobre o mundo"]];
const AXES = [["p", "Prazer"], ["a", "Ativação"], ["d", "Dominância"]];

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function trace(items) {
  return h("div", { class: "trace" }, ...items.map((t) => {
    if (t.kind !== "turn") return h("p", { class: "muted small", text: t.ref || "" });
    if (t.hidden) return h("p", { class: "muted", text: "A fala está oculta (dashboard.transcripts desligado)." });
    if (t.missing) return h("p", { class: "muted", text: "A fala de origem não está mais guardada." });
    return h("div", {},
      h("p", { class: "label", text: `O que o Petrus escreveu · ${when(t.at)}` }),
      h("blockquote", { text: t.user }),
      t.assistant ? h("p", { class: "label", text: "O que ela respondeu" }) : null,
      t.assistant ? h("blockquote", { class: "her", text: t.assistant }) : null,
    );
  }));
}

async function voiceCard() {
  const v = await api("/voice");
  if (!v.active) return card("Como estou falando agora", empty("Os sentimentos estão desligados."));
  const [p, a, d] = v.point;
  return card("Como estou falando agora",
    h("p", {}, v.mood ? `Estou ${v.mood}.` : "Estou no meu normal.", " ",
      v.forced_until ? badge(`forçado até ${when(v.forced_until)}`, "live") : null),
    h("p", { class: "muted small", text: `prazer ${p.toFixed(2)} · ativação ${a.toFixed(2)} · dominância ${d.toFixed(2)}` }),
    v.lines.length
      ? h("ul", { class: "plain" }, ...v.lines.map((line) => h("li", { text: line })))
      : h("p", { class: "muted", text: "Nenhuma ordem de tom: ela fala no jeito de sempre." }),
  );
}

async function moodCard() {
  const box = h("div", {});
  async function draw() {
    const mood = await api("/mood", { days: state.days });
    clear(box).append(
      ...AXES.map(([key, name]) => h("div", {},
        h("p", { class: "label", text: name }),
        lineChart(mood.points.map((p) => ({ at: p.at, value: p[key] })), { label: `${name} do humor` }),
      )),
      h("div", { class: "chips" }, ...[7, 14, 30].map((d) => chip(`${d} dias`, state.days === d, async () => { state.days = d; await draw(); }))),
    );
  }
  await draw();
  return card("Humor no espaço PAD", h("p", { class: "muted small", text: "Onde o humor está: o ponto de repouso (do temperamento) mais o que as emoções empurraram." }), box);
}

async function traitsCard() {
  const data = await api("/persona");
  if (!data.state) return card("Aspectos da personalidade", empty("Ainda não há uma personalidade guardada."));
  const history = data.history;
  const names = Object.keys(history.at(-1).traits).sort();
  if (!state.trait || !names.includes(state.trait)) state.trait = names[0];
  const chart = h("div", {});
  const why = h("div", { class: "card detail", "aria-live": "polite" }, empty("Escolha uma versão para ver por que mudei."));

  async function showWhy(version) {
    clear(why).append(h("p", { class: "muted", text: "Carregando…" }));
    const w = await api("/evolution/why", { version });
    clear(why).append(
      h("h3", { text: `Versão ${w.version} · ${when(w.at)}` }),
      w.recent.length ? h("p", {}, h("span", { class: "label", text: "O que me marcou: " }), w.recent.join("; ")) : null,
      w.trace.length ? trace(w.trace) : empty("Esta versão não cita conversas (migração, reversão ou maturação)."),
    );
  }

  function draw() {
    const values = history.map((v) => ({ at: v.at, value: v.traits[state.trait] ?? 0 }));
    const bases = history.map((v) => ({ at: v.at, value: (v.baselines || {})[state.trait] ?? 0 }));
    clear(chart).append(
      h("div", { class: "chips" }, ...names.map((n) => chip(n, state.trait === n, () => { state.trait = n; draw(); }))),
      h("p", { class: "label", text: "O traço (agora)" }),
      lineChart(values, { min: 0, max: 1, label: `${state.trait} ao longo das versões` }),
      h("p", { class: "label", text: "O jeito de sempre (ponto de partida, que amadurece)" }),
      lineChart(bases, { min: 0, max: 1, label: `ponto de partida de ${state.trait}` }),
      h("div", { class: "list" }, ...[...history].reverse().slice(0, 15).map((v) => {
        const now = v.traits[state.trait];
        return h("button", { type: "button", class: "row link", onclick: () => showWhy(v.version) },
          h("span", { text: `v${v.version} · ${when(v.at)}` }),
          h("span", { text: v.reason }),
          h("span", { text: now === undefined ? "-" : now.toFixed(2) }),
        );
      })),
    );
  }
  draw();
  return card("Aspectos da personalidade (Big Five + ironia)",
    h("p", { class: "muted small", text: `${data.words?.join(" ") || ""}` }), chart, why);
}

async function beliefsCard() {
  const list = h("div", {});
  const panel = h("div", { class: "card detail", "aria-live": "polite" }, empty("Escolha uma crença para ver a curva de confiança."));
  const layers = h("div", { class: "chips" });

  async function show(id) {
    clear(panel).append(h("p", { class: "muted", text: "Carregando…" }));
    const b = await api(`/beliefs/${id}`);
    clear(panel).append(
      h("h3", { text: b.statement }),
      h("p", { class: "muted small", text: `${b.words} · ${Math.round(b.confidence * 100)}% · ${b.sources} fonte(s) · ${b.status}` }),
      b.domains.length ? h("p", {}, h("span", { class: "label", text: "Sites que dizem: " }), b.domains.join(", ")) : null,
      h("p", { class: "label", text: "A confiança ao longo do tempo" }),
      b.history.length > 1
        ? lineChart(b.history.map((p) => ({ at: p.at, value: p.confidence })), { min: 0, max: 1, label: "confiança da crença" })
        : empty("Ainda há um ponto só: a curva aparece com a próxima evidência."),
      h("div", { class: "list" }, ...b.history.slice().reverse().map((p) =>
        h("div", { class: "row" }, h("span", { text: when(p.at) }), h("span", { text: p.cause }), h("span", { text: `${Math.round(p.confidence * 100)}%` })))),
    );
  }

  async function draw() {
    const data = await api("/beliefs", { about: state.layer, order: "confidence", limit: 30 });
    clear(layers).append(...LAYERS.map(([id, name]) => chip(name, state.layer === id, async () => { state.layer = id; await draw(); })));
    clear(list);
    if (!data.items.length) list.append(empty("Nenhuma crença nesta camada ainda."));
    for (const b of data.items) {
      list.append(h("button", { type: "button", class: "row link", onclick: () => show(b.id) },
        h("span", { text: b.statement }), h("span", {}, badge(b.words, b.status === "retired" ? "" : "live")), bar(b.confidence)));
    }
  }
  await draw();
  return card("Crenças por camada", layers, list, panel);
}

export async function render() {
  const root = h("div", {});
  root.append(h("h1", { text: "Evolução" }), h("p", { class: "muted", text: "Como ela muda: humor, personalidade e crenças, com o que moveu cada passo." }));
  for (const build of [voiceCard, moodCard, traitsCard, beliefsCard]) {
    try {
      root.append(await build());
    } catch (error) {
      root.append(card("", empty(error?.message || "Não consegui carregar esta parte.")));
    }
  }
  return root;
}
