import { api } from "/assets/api.js";
import { radarChart } from "/assets/charts.js";
import { ago, badge, bar, card, empty, h, percent, signedBar, when } from "/assets/ui.js";

const PHASES = { done: "ok", skipped: "pulada", error: "erro", budget: "orçamento" };

function counter(label, value, hint) {
  return h("div", { class: "stat" }, h("span", { class: "num", text: value ?? "—" }), h("span", { class: "label", text: label }), hint ? h("span", { class: "muted small", text: hint }) : null);
}

function feelings(affect) {
  const all = [...affect.feelings, ...affect.grievances];
  if (!all.length && !affect.echoes.length) return empty("Nenhum sentimento forte agora.");
  const rows = all.map((f) =>
    h("li", {},
      h("span", { class: "kind", text: f.grievance ? "mágoa" : f.kind }),
      badge(f.origin, f.origin === "conversa" ? "live" : ""),
      bar(f.strength, f.valence < 0 ? "neg" : "pos"),
      h("span", { class: "muted small", text: f.strength.toFixed(2) }),
    ),
  );
  const echoes = affect.echoes.map((f) => h("li", { class: "muted" }, `eco: ${f.kind}`));
  return h("ul", { class: "plain" }, ...rows, ...echoes);
}

function lastDream(run) {
  if (!run) return empty("Ela ainda não sonhou.");
  const seconds = run.seconds == null ? "—" : `${Math.round(run.seconds / 60)} min`;
  return h("div", {},
    h("p", {}, badge(run.status, run.status === "done" ? "ok" : "warn"), ` ${when(run.started_at)} (${ago(run.started_at)})`),
    h("p", { class: "muted small", text: `${seconds} · ${run.tokens_used.toLocaleString("pt-BR")} tokens · ${run.sessions_read} conversa(s)` }),
    h("ul", { class: "plain phases" },
      ...run.phases.map((p) => h("li", {}, h("span", { text: p.name }), badge(PHASES[p.status] || p.status, p.status === "done" ? "ok" : p.status === "skipped" ? "" : "warn"), h("span", { class: "muted small", text: p.summary || "" }))),
    ),
  );
}

export async function render() {
  const o = await api("/overview");
  const grid = h("div", { class: "grid" });

  if (o.v2.enabled) {
    const a = o.affect;
    grid.append(
      card("Como ela está agora",
        a.active ? h("p", { class: "lead", text: a.lines.length ? a.lines.join(" ") : "Tudo calmo." }) : empty("Os sentimentos estão desligados."),
        h("div", { class: "row" }, h("span", { class: "label", text: "Humor" }), signedBar(a.mood), h("span", { class: "muted small", text: a.mood.toFixed(2) })),
        feelings(a),
      ),
    );
    const p = o.persona;
    grid.append(
      card("Personalidade",
        p
          ? h("div", {},
              radarChart(p.traits, { label: "Traços da personalidade" }),
              h("p", { class: "muted small", text: `Versão ${p.version} · desvio do jeito de sempre ${p.drift.toFixed(3)}. Linha cheia = agora; tracejada = jeito de partida.` }),
              p.recent.length ? h("p", { text: `Ultimamente: ${p.recent.join("; ")}` }) : null,
            )
          : empty("Ela ainda não tem uma versão gravada (o primeiro sonho cria)."),
      ),
    );
  } else {
    grid.append(card("Memória V2", empty("Desligada (memory.v2.enabled).")));
  }

  grid.append(
    card("Em números",
      h("div", { class: "stats" },
        counter("memórias ativas", o.v1.enabled ? o.v1.active : null, o.v1.enabled ? `${o.v1.refinada} refinadas · ${o.v1.bruta} brutas` : "desligada"),
        counter("crenças", o.v2.beliefs),
        counter("temas de estudo", o.v2.interests),
        counter("notas de estudo", o.v2.notes, o.v2.questions != null ? `${o.v2.questions} perguntas em aberto` : ""),
        counter("sentimentos", o.v2.affects),
        counter("sonhos", o.v2.dreams),
      ),
    ),
  );
  if (o.v2.enabled) grid.append(card("Último sonho", lastDream(o.lastDream)));

  return h("div", {}, h("h1", { text: "Visão geral" }), grid);
}
