import { post } from "/assets/api.js";
import { clear, h } from "/assets/ui.js";

// The dashboard's writes on memory (ADR-028). A destructive one opens a small form that asks for
// the id (or a word) to be typed; the server checks it again, so this is only the polite half.

export function dangerAction(label, expected, path, onDone, params = {}) {
  const slot = h("span", {});
  const button = h("button", {
    type: "button", class: "ghost", text: label,
    onclick: () => {
      const input = h("input", { type: "text", placeholder: expected, "aria-label": `Digite ${expected} para confirmar`, autocomplete: "off" });
      const error = h("p", { class: "error small", role: "alert", hidden: true });
      const form = h("form", { class: "confirm" },
        h("label", { text: `Digite «${expected}» para confirmar` }), input,
        h("button", { type: "submit", text: "Confirmar" }), error);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hidden = true;
        try {
          await post(path, { ...params, confirm: input.value.trim() });
          await onDone();
        } catch (e) {
          error.textContent = e.message;
          error.hidden = false;
        }
      });
      clear(slot).append(form);
    },
  });
  return h("div", {}, button, slot);
}

export function quickAction(label, path, params, onDone) {
  const error = h("p", { class: "error small", role: "alert", hidden: true });
  const button = h("button", {
    type: "button", class: "ghost", text: label,
    onclick: async () => {
      error.hidden = true;
      try {
        await post(path, typeof params === "function" ? params() : params);
        await onDone();
      } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
      }
    },
  });
  return h("span", {}, button, error);
}
