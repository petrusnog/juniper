import { api } from "/assets/api.js";
import { ago, badge, bar, card, clear, empty, h, when } from "/assets/ui.js";

const STATUS = { done: ["ok", ""], partial: ["parcial", "warn"], running: ["rodando", "live"], failed: ["falhou", "warn"] };
const PHASE = { done: ["ok", ""], skipped: ["pulada", ""], error: ["erro", "warn"], budget: ["orçamento", "warn"] };
const journal = { days: 30, kind: "" };
const KIND = { dream: "sonho", study: "estudo", persona: "personalidade", note: "nota" };

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function minutes(seconds) {
  if (seconds == null) return "—";
  return seconds < 90 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
}

function run(r, biggest) {
  const [label, kind] = STATUS[r.status] || [r.status, ""];
  return h("section", { class: "card run" },
    h("h2", {}, when(r.started_at), " ", badge(label, kind), r.dry_run ? badge("ensaio (dry-run)", "live") : null),
    h("p", { class: "muted small", text: `${ago(r.started_at)} · ${minutes(r.seconds)} · ${r.sessions_read} conversa(s) lida(s)` }),
    h("div", { class: "row" }, h("span", { class: "label", text: "Tokens" }), bar(biggest ? r.tokens_used / biggest : 0), h("span", { class: "muted small", text: r.tokens_used.toLocaleString("pt-BR") })),
    r.error ? h("p", { class: "error", text: r.error }) : null,
    h("ul", { class: "plain phases" }, ...r.phases.map((p) => {
      const [text, k] = PHASE[p.status] || [p.status, ""];
      return h("li", { class: "phase" },
        h("span", { class: "kind", text: p.name }), badge(text, k),
        h("span", { class: "muted small", text: `${p.changed ?? 0} mudança(s) · ${(p.tokens ?? 0).toLocaleString("pt-BR")} tokens` }),
        p.summary ? h("span", { class: "phase-summary", text: p.summary }) : null,
        p.error ? h("span", { class: "error small", text: p.error }) : null,
      );
    })),
  );
}

export async function render() {
  const runs = await api("/dreams", { limit: 20 });
  const biggest = Math.max(0, ...runs.items.map((r) => r.tokens_used));
  const list = h("div", {});
  const entries = h("div", {});
  const filters = h("div", {});

  async function drawJournal() {
    const data = await api("/journal", { days: journal.days, kind: journal.kind, limit: 100 });
    const pick = (key, value) => async () => { journal[key] = value; await drawJournal(); };
    clear(filters).append(
      h("div", { class: "chips", role: "group", "aria-label": "Período" },
        ...[[7, "7 dias"], [30, "30 dias"], [90, "90 dias"]].map(([v, l]) => chip(l, journal.days === v, pick("days", v)))),
      h("div", { class: "chips", role: "group", "aria-label": "Tipo" },
        ...[["", "Tudo"], ["dream", "Sonhos"], ["study", "Estudos"], ["persona", "Personalidade"]].map(([v, l]) => chip(l, journal.kind === v, pick("kind", v)))),
    );
    clear(entries);
    if (!data.items.length) entries.append(empty("Nada no diário nesse período."));
    for (const j of data.items) {
      entries.append(h("article", { class: "entry" },
        h("p", { class: "muted small" }, `${when(j.at)} `, badge(KIND[j.kind] || j.kind)),
        h("p", { class: "pre-line", text: j.text })));
    }
  }

  if (!runs.items.length) list.append(card("", empty("Ela ainda não sonhou. O primeiro sonho roda às 03:00, ou à mão com `juniper memory dream`.")));
  runs.items.forEach((r) => list.append(run(r, biggest)));
  await drawJournal();
  return h("div", {}, h("h1", { text: "Sonhos" }), list, h("h2", { class: "section-title", text: "Diário" }), card("", filters, entries));
}
