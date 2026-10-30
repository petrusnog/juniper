import { api } from "/assets/api.js";
import { badge, bar, card, empty, h, when } from "/assets/ui.js";

// Health of the server and what it costs (voice cap, music quota, dream tokens). Read only.

function fraction(used, limit) {
  return limit ? Math.min(1, used / limit) : 0;
}

function meter(label, used, limit, unit) {
  if (used == null) return h("p", { class: "muted", text: `${label}: sem leitura` });
  const text = limit ? `${used.toLocaleString("pt-BR")} de ${limit.toLocaleString("pt-BR")} ${unit}` : `${used.toLocaleString("pt-BR")} ${unit}`;
  const over = limit && used / limit >= 0.9;
  return h("div", { class: "row" }, h("span", { class: "label", text: label }), bar(fraction(used, limit), over ? "neg" : ""), h("span", { class: "muted small", text }));
}

function uptime(seconds) {
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} dias`;
}

export async function render() {
  const [health, costs] = await Promise.all([api("/health"), api("/costs")]);
  const layers = h("ul", { class: "plain" }, ...Object.entries(health.layers).map(([name, on]) =>
    h("li", {}, badge(on ? "ligada" : "desligada", on ? "" : "warn"), ` ${name}`)));
  const tools = health.tools.length
    ? h("ul", { class: "plain" }, ...health.tools.map((t) => h("li", {}, h("span", { class: "kind", text: t.name }), ` ${t.tier} · ${t.locality}`, t.offered ? "" : badge("não oferecida", "warn"))))
    : empty("Nenhuma tool registrada neste processo.");
  const auth = h("ul", { class: "plain" },
    h("li", { text: `Token estático (legacy_token): ${health.auth.legacy_token ? "ainda vale" : "desligado"}` }),
    h("li", { text: `Cadastro de tester: ${health.auth.allow_testers ? "liberado" : "desligado"}` }),
    h("li", { text: `Login no Telegram: ${health.auth.telegram_login ? "exigido" : "não exigido"}` }));
  const providers = costs.providers;
  const failovers = Object.entries(providers.failovers);
  const runs = costs.dreams.runs;
  return h("div", {},
    h("h1", { text: "Saúde e custos" }),
    card("Servidor",
      h("p", {}, badge(health.status, health.status === "ok" ? "" : "warn"), ` versão ${health.version} · modo ${health.mode} · no ar há ${uptime(health.uptime_seconds)}`),
      h("p", { class: "muted small", text: `Memória: ${health.memory || "?"}` })),
    card("Camadas", layers),
    card("Acesso", auth),
    card("Custos",
      meter("Voz (mês)", costs.voice.used, costs.voice.limit, "caracteres"),
      meter("Música (dia)", costs.music.used, costs.music.limit, "unidades de cota"),
      meter("Sonho (últimas execuções)", costs.dreams.total ?? null, costs.dreams.budget ? costs.dreams.budget * Math.max(1, runs.length) : 0, "tokens"),
      runs.length ? h("ul", { class: "plain" }, ...runs.slice(0, 7).map((r) => h("li", { class: "muted small", text: `${when(r.started_at)} · ${r.status} · ${r.tokens.toLocaleString("pt-BR")} tokens` }))) : null),
    card(`Provedores (${providers.days} dias)`,
      failovers.length ? h("ul", { class: "plain" }, ...failovers.map(([name, n]) => h("li", { text: `${name}: ${n} troca(s) para o próximo (failover)` }))) : h("p", { class: "muted", text: "Nenhum failover." }),
      h("p", { class: "muted small", text: `Erros por dia: ${Object.entries(providers.errors_per_day).map(([d, n]) => `${d}=${n}`).join(", ") || "nenhum"}.` })),
    card(`Tools (${health.tools.length})`, tools));
}
