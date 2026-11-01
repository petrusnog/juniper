import { api, post } from "/assets/api.js";
import { badge, card, clear, empty, h, when } from "/assets/ui.js";

// Background tasks the owner starts here: the dream and the reindex (ADR-028). One at a time.
const NAMES = { dream: "Sonho", reindex: "Reindexação" };
const STATE = { running: ["rodando", "live"], done: ["ok", ""], failed: ["falhou", "warn"] };
let timer = null;

function run(job) {
  const [text, kind] = STATE[job.state] || [job.state, ""];
  const flags = Object.entries(job.params || {}).filter(([, v]) => v).map(([k]) => (k === "dry_run" ? "ensaio" : k === "force" ? "tudo" : k));
  return h("li", { class: "run" },
    h("span", { class: "kind", text: NAMES[job.name] || job.name }), " ", badge(text, kind),
    flags.length ? badge(flags.join(", "), "live") : null,
    h("span", { class: "muted small", text: ` ${when(job.started_at)}${job.finished_at ? ` → ${when(job.finished_at)}` : ""}` }),
    job.error ? h("p", { class: "error small", text: job.error }) : null,
    job.result ? h("pre", { class: "block", text: job.result }) : null);
}

export async function render() {
  if (timer) clearInterval(timer);
  const holder = h("div", {});
  const error = h("p", { class: "error small", role: "alert", hidden: true });

  async function start(name, params) {
    error.hidden = true;
    try {
      await post(`/jobs/${name}`, params);
      await draw();
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  }

  async function draw() {
    const data = await api("/jobs");
    const busy = !!data.active;
    const button = (label, name, params) => h("button", { type: "button", class: "ghost", disabled: busy, text: label, onclick: () => start(name, params) });
    clear(holder).append(
      card("Rodar agora",
        h("p", { class: "muted small", text: "Uma tarefa por vez. O servidor continua conversando enquanto ela roda. Evite o horário do sonho da noite (memory.v2.night.at)." }),
        h("p", {}, button("Sonho (ensaio, não grava)", "dream", { dry: 1 }), " ", button("Sonho", "dream", {})),
        h("p", {}, button("Reindexar o que mudou", "reindex", {}), " ", button("Reindexar tudo", "reindex", { force: 1 })),
        error),
      data.active ? card("Agora", h("ul", { class: "plain" }, run(data.active))) : null,
      card("Últimas execuções", data.history.length ? h("ul", { class: "plain" }, ...data.history.map(run)) : empty("Nada rodou desde que o servidor subiu.")));
    if (busy && !timer) {
      timer = setInterval(() => { if (!document.body.contains(holder)) { clearInterval(timer); timer = null; } else draw().catch(() => {}); }, 3000);
    } else if (!busy && timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  await draw();
  return h("div", {}, h("h1", { text: "Tarefas" }), h("p", { class: "muted", text: "O sonho e a reindexação, que antes só rodavam por comando na VPS." }), holder);
}
