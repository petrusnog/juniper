import { clear, h } from "/assets/ui.js";
import { pages } from "/assets/pages/index.js";

const nav = document.getElementById("nav");
const view = document.getElementById("view");

function current() {
  const id = window.location.hash.replace(/^#\//, "").split("/")[0];
  return pages.find((p) => p.id === id) || pages[0];
}

function renderNav(active) {
  clear(nav);
  for (const page of pages) {
    nav.append(
      h("a", {
        href: `#/${page.id}`,
        class: page.id === active.id ? "active" : "",
        "aria-current": page.id === active.id ? "page" : null,
        text: page.title,
      }),
    );
  }
}

async function render() {
  const page = current();
  renderNav(page);
  document.title = `${page.title} — Juniper`;
  clear(view).append(h("p", { class: "muted", text: "Carregando…" }));
  try {
    const module = await import(page.module);
    const rest = window.location.hash.replace(/^#\//, "").split("/").slice(1);
    const content = await module.render(rest);
    clear(view).append(content);
  } catch (error) {
    clear(view).append(h("p", { class: "error", role: "alert", text: error.message || "Algo deu errado." }));
  }
  view.focus({ preventScroll: true });
}

document.getElementById("logout").addEventListener("click", async () => {
  await fetch("/api/logout");
  window.location.href = "/login";
});
window.addEventListener("hashchange", render);
render();
