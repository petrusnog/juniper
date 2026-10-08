/* Layout compartilhado: sidebar gerada a partir de uma única lista.
   Para registrar uma feature nova, edite só FEATURES abaixo (e crie a página). */
const FEATURES = [
  { group: "LLM", id: "T-011", title: "Vocabulário LLM", file: "T-011.html",
    desc: "Tipos, erros e o contrato LLMProvider.", status: "done" },
  { group: "LLM", id: "T-012", title: "OllamaProvider", file: "T-012.html",
    desc: "Provider local via HTTP.", status: "done" },
  { group: "LLM", id: "T-013", title: "GroqProvider", file: "T-013.html",
    desc: "Provider em nuvem via SDK.", status: "done" },
  { group: "LLM", id: "T-014", title: "Streaming", file: "T-014.html",
    desc: "Tokens em tempo real.", status: "done" },
  { group: "LLM", id: "T-015", title: "LLMRouter", desc: "Failover Groq → Ollama.", status: "planned" },
  { group: "Tools", id: "T-020", title: "Base de Tools", desc: "Tool, Tier, ToolResult.", status: "planned" },
  { group: "Tools", id: "T-021", title: "Registry", desc: "Auto-descoberta de tools.", status: "planned" },
  { group: "Agente", id: "T-030", title: "Agent loop", desc: "Decide → chama tool → reavalia.", status: "planned" },
  { group: "Clientes", id: "T-040", title: "CLI", desc: "juniper chat completo.", status: "planned" },
];

(function () {
  const page = location.pathname.split("/").pop() || "index.html";
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  window.JUNIPER_FEATURES = FEATURES;

  const groups = [...new Set(FEATURES.map((f) => f.group))];
  let nav = `<a class="brand" href="index.html">Juniper · Livro de bolso</a>
    <div class="sub">Referências técnicas por feature</div>
    <a class="item ${page === "index.html" ? "active" : ""}" href="index.html">Índice</a>`;
  for (const g of groups) {
    nav += `<h4>${esc(g)}</h4>`;
    for (const f of FEATURES.filter((x) => x.group === g)) {
      const label = `${f.id} · ${esc(f.title)}`;
      nav += f.file
        ? `<a class="item ${page === f.file ? "active" : ""}" href="${f.file}">${label}</a>`
        : `<span class="item soon">${label}</span>`;
    }
  }

  const main = document.getElementById("content");
  const shell = document.createElement("div");
  shell.className = "shell";
  const side = document.createElement("nav");
  side.className = "side";
  side.innerHTML = nav;
  main.parentNode.insertBefore(shell, main);
  shell.append(side, main);

  // Cards do índice (se a página tiver o contêiner)
  const holder = document.getElementById("feature-cards");
  if (holder) {
    holder.innerHTML = FEATURES.map((f) => {
      const tag = f.status === "done" ? `<span class="tag g">pronta</span>` : `<span class="tag a">planejada</span>`;
      const body = `${tag} <span class="tag b">${f.id}</span><h3>${esc(f.title)}</h3><p>${esc(f.desc)}</p>`;
      return f.file ? `<a class="fcard" href="${f.file}">${body}</a>` : `<div class="fcard soon">${body}</div>`;
    }).join("");
  }
})();
