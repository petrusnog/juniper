import { api } from "/assets/api.js";
import { dangerAction, quickAction } from "/assets/actions.js";
import { lineChart, radarChart } from "/assets/charts.js";
import { badge, bar, card, clear, empty, h, when } from "/assets/ui.js";

function section(summary, ...children) {
  return h("details", { class: "fold" }, h("summary", { text: summary }), ...children);
}

function personality(p) {
  if (!p.state) return empty("Ela ainda não tem uma versão gravada.");
  const traitNames = p.state.traits.map((t) => t.name);
  const chart = h("div", {});
  const pick = h("select", { "aria-label": "Traço para a linha do tempo" }, ...traitNames.map((n) => h("option", { value: n, text: n })));
  const drawLine = () => {
    const name = pick.value;
    const points = p.history.map((v) => ({ at: v.at, value: v.traits[name] })).filter((x) => x.value != null);
    clear(chart);
    if (points.length < 2) chart.append(empty("Só há uma versão da personalidade até agora; a linha aparece quando houver mais."));
    else chart.append(lineChart(points, { min: 0, max: 1, label: `Traço ${name} por versão` }));
  };
  pick.addEventListener("change", drawLine);
  drawLine();
  return h("div", {},
    radarChart(p.state.traits, { label: "Traços da personalidade" }),
    h("p", { class: "muted small", text: `Versão ${p.state.version} · desvio ${p.state.drift.toFixed(3)}${p.drift_warning ? " · ATENÇÃO: passou do limite de deriva" : ""}` }),
    p.words?.length ? h("ul", { class: "plain" }, ...p.words.map((w) => h("li", { text: w }))) : null,
    h("h3", { text: "Um traço ao longo das versões" }), pick, chart,
  );
}

function beliefs(page, onPick, detailBox) {
  if (!page.items.length) return empty("Nenhuma crença ainda.");
  return h("div", {}, ...page.items.map((b) =>
    h("button", { type: "button", class: "row-item belief", onclick: () => onPick(b.id) },
      h("span", { class: "kind", text: b.statement }),
      badge(b.words),
      bar(b.confidence, "pos"),
      h("span", { class: "muted small", text: `${Math.round(b.confidence * 100)}% · ${b.sources} fonte(s)` }),
    )));
}

async function showBelief(box, id) {
  const b = await api(`/beliefs/${encodeURIComponent(id)}`);
  clear(box).append(
    h("h3", { text: b.statement }),
    h("p", { class: "muted small", text: `${b.status} · tópico ${b.topic} · confiança ${b.confidence} · ${b.evidence_for} a favor, ${b.evidence_against} contra${b.user_confirmed ? ` · você ${b.user_confirmed}` : ""}` }),
    dangerAction("Esquecer esta opinião", b.id, `/memory/beliefs/${b.id}/forget`, () => { window.dispatchEvent(new HashChangeEvent("hashchange")); }),
    h("ul", { class: "plain" }, ...b.evidence.map((e) => h("li", {}, badge(e.side === "for" ? "a favor" : "contra", e.side === "for" ? "" : "warn"), h("span", { text: e.kind }), h("span", { class: "muted small", text: `${when(e.at)} · conversa ${e.session_id || "—"} turno ${e.turn ?? "—"} · peso ${e.weight}` })))),
  );
}

function concernRow(c) {
  const input = h("input", { type: "number", min: "0", max: "1", step: "0.05", value: c.weight.toFixed(2), "aria-label": `Peso de ${c.name}` });
  return h("li", {}, h("span", { class: "kind", text: c.name }), bar(c.weight),
    h("span", { class: "muted small", text: `${c.weight.toFixed(2)} (base ${c.baseline.toFixed(2)}) ` }), input,
    quickAction("Salvar peso", `/memory/concerns/${c.id}/weight`, () => ({ value: input.value, reason: "ajuste pelo dashboard" }),
      () => { window.dispatchEvent(new HashChangeEvent("hashchange")); }));
}

function simulator() {
  const input = h("input", { type: "text", placeholder: "Escreva uma mensagem para ver o que a V2 acrescentaria ao prompt…", "aria-label": "Mensagem para simular", maxlength: "2000" });
  const out = h("div", { "aria-live": "polite" });
  const run = async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    clear(out).append(h("p", { class: "muted", text: "Montando…" }));
    try {
      const r = await api("/prompt", { message });
      clear(out);
      if (!r.sections.length) out.append(empty("A V2 não acrescentaria nada a essa mensagem."));
      for (const s of r.sections) {
        out.append(h("h3", {}, s.name, " ", badge(`${s.tokens} tokens`)), h("pre", { class: "block", text: s.text }));
      }
      out.append(h("p", { class: "muted small", text: `Total: ${r.tokens} tokens. Nada foi gravado. Os fatos da memória v1 não entram aqui.` }));
    } catch (error) {
      clear(out).append(h("p", { class: "error", role: "alert", text: error.message }));
    }
  };
  return h("form", { onsubmit: run }, input, h("button", { type: "submit", text: "Simular" }), out);
}

export async function render() {
  const [core, persona, concerns, belief] = await Promise.all([
    api("/core"),
    api("/persona").catch((e) => (e.status === 503 ? { state: null, history: [], off: true } : Promise.reject(e))),
    api("/concerns").catch(() => ({ items: [] })),
    api("/beliefs", { order: "confidence", limit: 100 }).catch(() => ({ items: [] })),
  ]);
  const detailBox = h("div", { class: "detail-box" }, empty("Escolha uma crença para ver as evidências."));
  const root = h("div", {}, h("h1", { text: "Memória central" }));

  root.append(
    card("Núcleo", h("p", { class: "muted small", text: "O texto fixo que vem antes de tudo (regras, honestidade, jeito de falar). O que é vivo (personalidade, humor, opiniões) vem depois, em blocos." }),
      section("Mostrar o núcleo", h("pre", { class: "block", text: core.core }))),
    card("Perfil", core.profile ? h("div", {}, h("p", { text: core.profile.text }), h("p", { class: "muted small", text: `Atualizado em ${when(core.profile.updated_at)}` })) : empty("Sem perfil guardado.")),
    card("Personalidade viva", personality(persona), persona.state ? h("p", {}, quickAction("Voltar à versão anterior", "/memory/persona/revert", {}, () => { window.dispatchEvent(new HashChangeEvent("hashchange")); }), h("span", { class: "muted small", text: " Cria uma versão nova com o jeito da anterior; o histórico fica." })) : null),
    card("O que ela acha de você (crenças)", beliefs(belief, (id) => showBelief(detailBox, id)), detailBox),
    card("O que importa para ela",
      concerns.items.length
        ? h("ul", { class: "plain" }, ...concerns.items.map((c) => concernRow(c)))
        : empty("Sem preocupações registradas.")),
    card("Simulador do prompt", h("p", { class: "muted small", text: "Digite uma mensagem e veja, bloco a bloco, o que a Memória V2 acrescentaria ao prompt dela. É só leitura." }), simulator()),
  );
  return root;
}
