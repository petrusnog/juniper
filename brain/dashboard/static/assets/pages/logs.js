import { api } from "/assets/api.js";
import { badge, card, clear, empty, h } from "/assets/ui.js";

// The technical log (what `juniper logs` shows), filtered. Read only; secrets are masked by the server.
const view = { days: 1, errors: false, q: "", event: "" };
const LEVEL = { error: ["erro", "warn"], http: ["http", ""], info: ["info", "live"] };

function chip(label, active, onclick) {
  return h("button", { type: "button", class: `chip ${active ? "on" : ""}`.trim(), "aria-pressed": active ? "true" : "false", onclick, text: label });
}

function line(r) {
  const [text, kind] = LEVEL[r.level] || [r.level, ""];
  return h("li", { class: "log-line" },
    h("span", { class: "muted small", text: `${r.clock} ` }),
    r.level ? badge(text, kind) : null,
    h("span", { class: "kind", text: ` ${r.event} ` }),
    r.session ? h("span", { class: "muted small", text: `${r.session} ` }) : null,
    h("span", { class: "small", text: r.summary }),
  );
}

export async function render() {
  const holder = h("div", {});
  const search = h("input", { type: "search", placeholder: "buscar no texto", value: view.q, "aria-label": "Buscar no log" });
  const events = h("input", { type: "text", placeholder: "eventos (ex.: tool,failover)", value: view.event, "aria-label": "Eventos" });

  async function draw() {
    const data = await api("/logs", { days: view.days, errors: view.errors ? 1 : "", q: view.q, event: view.event, limit: 200 });
    const list = h("ul", { class: "plain" }, ...data.items.map(line));
    const filters = h("div", { class: "chips", role: "group", "aria-label": "Filtros" },
      ...[[1, "hoje"], [3, "3 dias"], [7, "7 dias"]].map(([d, l]) => chip(l, view.days === d, async () => { view.days = d; await draw(); })),
      chip("só erros", view.errors, async () => { view.errors = !view.errors; await draw(); }));
    clear(holder).append(card("", filters, h("p", {}, search, " ", events, " ", h("button", { type: "button", text: "Filtrar", onclick: async () => { view.q = search.value.trim(); view.event = events.value.trim(); await draw(); } })),
      data.items.length ? list : empty("Nada no log com esses filtros."),
      h("p", { class: "muted small", text: `${data.items.length} evento(s), do mais novo ao mais antigo (no máximo 200). Dias com arquivo: ${data.available.join(", ") || "nenhum"}.` })));
  }

  await draw();
  return h("div", {}, h("h1", { text: "Logs" }), h("p", { class: "muted", text: "O log técnico do servidor, o mesmo de `juniper logs`, com segredos mascarados." }), holder);
}
