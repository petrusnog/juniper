import { api, post } from "/assets/api.js";
import { badge, card, clear, empty, h, when } from "/assets/ui.js";

const STATUSES = ["Pendente", "Enviado pelo Telegram", "Relembrado no chat de voz", "Vencido"];

export async function render() {
  const root = h("section", {}, h("h1", { text: "Lembretes" }));
  const notice = h("p", { role: "alert", class: "error" });
  const list = h("div");
  const q = h("input", { type: "search", "aria-label": "Pesquisar lembretes", placeholder: "Pesquisar" });
  const status = h("select", { "aria-label": "Status" }, h("option", { value: "", text: "Todos os status" }),
    ...STATUSES.map(s => h("option", { value: s, text: s })));
  const pager = h("p", { class: "muted" });
  let offset = 0;
  async function refresh() {
    try {
      const data = await api("/reminders", { q: q.value, status: status.value, offset, limit: 25 });
      clear(list);
      for (const item of data.items) list.append(row(item));
      if (!data.items.length) list.append(empty("Nenhum lembrete encontrado."));
      pager.textContent = `${data.total} lembretes · página ${Math.floor(offset / 25) + 1}`;
      prev.disabled = offset === 0;
      next.disabled = offset + 25 >= data.total;
      notice.textContent = "";
    } catch (error) { notice.textContent = error.message; }
  }
  function form(item = null) {
    const title = h("input", { required: true, maxlength: 200, value: item?.title || "", "aria-label": "Título" });
    // Use explicit ISO text: a browser-local datetime picker could silently change the agent timezone.
    const due = h("input", { required: true, value: item?.due_at || "", placeholder: "AAAA-MM-DD ou data/hora ISO", "aria-label": "Prazo" });
    const day = h("input", { value: item?.notify_at || "", placeholder: "AAAA-MM-DD ou data/hora ISO", "aria-label": "Quando avisar" });
    const notes = h("textarea", { maxlength: 500, "aria-label": "Observações", text: item?.notes || "" });
    const error = h("p", { class: "error", role: "alert" });
    const submit = h("button", { type: "submit", text: item ? "Salvar alterações" : "Criar lembrete" });
    const node = h("form", {}, h("label", { text: "Título" }), title,
      h("label", { text: "Prazo (sem offset, usa o fuso da Juniper)" }), due,
      h("label", { text: "Quando avisar (vazio = no prazo; só a data = 9h)" }), day,
      h("label", { text: "Observações" }), notes, submit, error);
    node.addEventListener("submit", async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        const changes = { title: title.value, due_at: due.value, notify_at: day.value, notes: notes.value };
        if (item) {
          // Preserve channel receipts on text-only edits.
          for (const key of Object.keys(changes)) if (changes[key] === (item[key] || "")) delete changes[key];
          // post() omits empty fields; one space explicitly clears optional notes/day.
          if (changes.notes === "") changes.notes = " ";
          if (changes.notify_at === "") changes.notify_at = " ";
        }
        await post(item ? `/reminders/${item.id}/update` : "/reminders", changes);
        if (item) node.remove(); else node.reset();
        await refresh();
      } catch (err) { error.textContent = err.message; }
      finally { submit.disabled = false; }
    });
    return node;
  }
  function row(item) {
    const body = h("div", {}, h("p", { text: `Prazo: ${when(item.due_at)} · aviso: ${when(item.notify_at)}` }),
      h("p", { text: item.notes }), h("p", { class: "muted small", text: `ID: ${item.id}` }),
      h("div", {}, ...item.statuses.map(s => badge(s, s === "Vencido" ? "warn" : ""))),
      h("p", { text: `Telegram: ${when(item.telegram_sent_at)} · Voz: ${when(item.voice_reminded_at)}` }));
    const edit = h("button", { type: "button", text: "Editar", onclick: () => {
      if (!body.querySelector("form")) body.append(form(item));
    }});
    const remove = h("button", { type: "button", text: "Excluir", onclick: async () => {
      if (!window.confirm(`Excluir o lembrete “${item.title}”? O evento da agenda será preservado.`)) return;
      remove.disabled = true;
      try { await post(`/reminders/${item.id}/delete`, { confirm: item.id }); await refresh(); }
      catch (error) { notice.textContent = error.message; remove.disabled = false; }
    }});
    body.append(edit, remove);
    if (item.event_id) body.append(h("p", { class: "muted", text: "Vinculado à agenda. Editar aqui altera apenas o lembrete." }));
    return card(item.title, body);
  }
  const prev = h("button", { type: "button", text: "Anterior", onclick: () => { offset = Math.max(0, offset - 25); refresh(); } });
  const next = h("button", { type: "button", text: "Próxima", onclick: () => { offset += 25; refresh(); } });
  const filter = h("form", {}, q, status, h("button", { type: "submit", text: "Filtrar" }));
  filter.addEventListener("submit", event => { event.preventDefault(); offset = 0; refresh(); });
  root.append(card("Novo lembrete", form()), filter, notice, list, pager, prev, next);
  await refresh();
  return root;
}
