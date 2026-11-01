import { api, post } from "/assets/api.js";
import { ago, badge, card, clear, empty, h, when } from "/assets/ui.js";

// Users and credentials (ADR-027). The client_secret appears once, here, and is gone on the next draw.

function secretBox(result, onclose) {
  const { user, credential, client_secret: secret } = result;
  const copy = async (text, button) => {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copiado";
    } catch (_) {
      button.textContent = "Selecione e copie";
    }
  };
  const line = (label, value) => {
    const button = h("button", { type: "button", class: "ghost" , text: "Copiar" });
    button.addEventListener("click", () => copy(value, button));
    return h("p", {}, h("span", { class: "label", text: `${label} ` }), h("code", { text: value }), " ", button);
  };
  return h("section", { class: "card", role: "alert" },
    h("h2", { text: `Credencial de ${user.name}` }),
    line("client_id", credential.client_id),
    line("client_secret", secret),
    h("p", { class: "error", text: result.warning }),
    h("button", { type: "button", text: "Já guardei", onclick: onclose }),
  );
}

function confirmForm(expected, verb, run) {
  const input = h("input", { type: "text", placeholder: expected, "aria-label": `Digite ${expected} para confirmar`, autocomplete: "off" });
  const error = h("p", { class: "error small", role: "alert", hidden: true });
  const form = h("form", { class: "confirm" },
    h("label", { text: `Digite «${expected}» para ${verb}` }), input,
    h("button", { type: "submit", text: `Confirmar: ${verb}` }), error);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    try {
      await run(input.value.trim());
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
    }
  });
  return form;
}

export async function render() {
  const root = h("div", {});
  let data;
  try {
    data = await api("/users");
  } catch (error) {
    if (error.status === 403) return empty("Só o dono administra usuários.");
    throw error;
  }

  async function draw(reveal = null) {
    data = await api("/users");
    clear(root);
    if (reveal) root.append(secretBox(reveal, () => draw()));
    root.append(createCard(), ...data.users.map(userCard));
  }
  const act = (path, params = {}) => async () => {
    const result = await post(path, params);
    await draw(result.client_secret ? result : null);
  };

  function createCard() {
    const name = h("input", { type: "text", required: true, maxlength: 60, "aria-label": "Nome" });
    const role = h("select", { "aria-label": "Papel" },
      h("option", { value: "tester", text: "tester (beta)" }));
    const label = h("input", { type: "text", value: "padrão", maxlength: 60, "aria-label": "Dispositivo" });
    const error = h("p", { class: "error small", role: "alert", hidden: true });
    const form = h("form", {}, h("label", { text: "Nome" }), name, h("label", { text: "Papel" }), role,
      h("label", { text: "Dispositivo" }), label, h("button", { type: "submit", text: "Cadastrar" }), error);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      error.hidden = true;
      try {
        const result = await post("/users", { name: name.value, role: role.value, label: label.value });
        await draw(result);
      } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
      }
    });
    const note = data.allow_testers
      ? h("p", { class: "muted small", text: "Atenção: um tester vê o mesmo cérebro que você (memória, conversas, agenda). O isolamento por usuário ainda não existe." })
      : h("p", { class: "muted small", text: "Cadastrar tester está desligado (deployment.auth.allow_testers: false no servidor)." });
    return card("Cadastrar usuário", note, data.allow_testers ? form : null);
  }

  function credentialRow(user, c) {
    const actions = c.revoked ? [] : [
      h("button", { type: "button", class: "ghost", text: "Rotacionar", onclick: () => toggle(c.client_id, "rotacionar", (v) => act(`/credentials/${c.client_id}/rotate`, { confirm: v })()) }),
      h("button", { type: "button", class: "ghost", text: "Revogar", onclick: () => toggle(c.client_id, "revogar", (v) => act(`/credentials/${c.client_id}/revoke`, { confirm: v })()) }),
    ];
    const slot = h("div", {});
    function toggle(expected, verb, run) {
      clear(slot).append(confirmForm(expected, verb, run));
    }
    return h("li", { class: "row-item" },
      h("code", { text: c.client_id }), " ", h("span", { text: c.label }), " ",
      badge(c.revoked ? "revogada" : "ativa", c.revoked ? "warn" : ""),
      h("span", { class: "muted small", text: ` criada ${when(c.created_at)} · último uso ${c.last_used_at ? ago(c.last_used_at) : "nunca"}` }),
      ...actions, slot);
  }

  function userCard(user) {
    const slot = h("div", {});
    const controls = [];
    if (user.role !== "owner") {
      controls.push(user.status === "active"
        ? h("button", { type: "button", class: "ghost", text: "Desativar", onclick: () => clear(slot).append(confirmForm(user.name, "desativar", (v) => act(`/users/${user.id}/disable`, { confirm: v })())) })
        : h("button", { type: "button", class: "ghost", text: "Reativar", onclick: act(`/users/${user.id}/enable`) }));
    }
    if (user.status === "active") {
      controls.push(h("button", { type: "button", class: "ghost", text: "Nova credencial", onclick: act(`/users/${user.id}/credentials`, { label: "novo dispositivo" }) }));
    }
    return card(null,
      h("h2", {}, user.name, " ", badge(user.role), " ", badge(user.status === "active" ? "ativo" : "desativado", user.status === "active" ? "" : "warn")),
      h("ul", { class: "plain" }, ...user.credentials.map((c) => credentialRow(user, c))),
      h("div", {}, ...controls), slot);
  }

  await draw();
  return root;
}
