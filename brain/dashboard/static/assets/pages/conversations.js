import { api } from "/assets/api.js";
import { ago, badge, card, clear, empty, h, when } from "/assets/ui.js";

const SIZE = 50;

async function thread(id) {
  const probe = await api(`/sessions/${encodeURIComponent(id)}`, { limit: 1 });
  let offset = Math.max(0, probe.total - SIZE); // the newest turns first
  const box = h("div", {});
  const pager = h("div", { class: "pager" });

  async function draw() {
    const data = await api(`/sessions/${encodeURIComponent(id)}`, { limit: SIZE, offset });
    clear(box);
    for (const t of data.items) {
      box.append(
        h("div", { class: "turn" },
          h("p", { class: "muted small", text: `turno ${t.index} · ${when(t.at)}` }),
          h("p", { class: "bubble me", text: t.user }),
          t.assistant ? h("p", { class: "bubble her", text: t.assistant }) : h("p", { class: "muted small", text: t.used_tools ? "(a resposta usou ferramentas e fica de fora)" : "(sem resposta)" }),
        ),
      );
    }
    clear(pager).append(
      h("button", { type: "button", class: "ghost", disabled: offset === 0, onclick: async () => { offset = Math.max(0, offset - SIZE); await draw(); window.scrollTo(0, 0); }, text: "‹ Anteriores" }),
      h("span", { class: "muted small", text: `${data.total ? offset + 1 : 0}–${Math.min(offset + SIZE, data.total)} de ${data.total}` }),
      h("button", { type: "button", class: "ghost", disabled: offset + SIZE >= data.total, onclick: async () => { offset += SIZE; await draw(); window.scrollTo(0, 0); }, text: "Seguintes ›" }),
    );
  }
  await draw();
  return h("div", {}, h("p", {}, h("a", { href: "#/conversas", text: "‹ Todas as conversas" })), h("h1", { text: "Conversa" }), h("p", { class: "muted small", text: id }), card("", box, pager));
}

export async function render(rest) {
  try {
    if (rest[0]) return await thread(rest[0]);
    const data = await api("/sessions", { limit: 100 });
    const list = h("div", {});
    if (!data.items.length) list.append(empty("Nenhuma conversa guardada ainda."));
    for (const s of data.items) {
      const pending = s.turns - s.read_by_dream;
      list.append(
        h("a", { class: "mem-item", href: `#/conversas/${encodeURIComponent(s.session_id)}` },
          h("span", { class: "mem-text", text: s.session_id }),
          h("span", { class: "mem-meta" },
            badge(`${s.turns} turno(s)`),
            pending > 0 ? badge(`${pending} para o próximo sonho`, "warn") : badge("lida pelo sonho"),
            h("span", { class: "muted small", text: s.last_turn_at ? `${when(s.last_turn_at)} (${ago(s.last_turn_at)})` : "" }),
          ),
        ),
      );
    }
    return h("div", {}, h("h1", { text: "Conversas" }), card("", list));
  } catch (error) {
    if (error.status === 404) {
      return h("div", {}, h("h1", { text: "Conversas" }), card("", empty("As conversas estão desligadas (dashboard.transcripts: false).")));
    }
    throw error;
  }
}
