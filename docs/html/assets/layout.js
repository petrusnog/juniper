/* Layout compartilhado: sidebar gerada a partir de uma única lista.
   Para registrar uma feature nova, edite só FEATURES abaixo (e crie a página). */
const FEATURES = [
  { group: "Visão geral", id: "Mapa", title: "Arquitetura em camadas", file: "architecture.html",
    desc: "Como a Juniper funciona por inteiro, e onde estamos.", status: "guide" },
  { group: "Visão geral", id: "Guia", title: "Instalação", file: "install.html",
    desc: "Instalar a Juniper e as dependências de desenvolvimento.", status: "guide" },
  { group: "Visão geral", id: "Guia", title: "Voz da Juniper", file: "voz.html",
    desc: "Como ela fala: prefixos, fala fixa x gerada por IA.", status: "guide" },
  { group: "LLM", id: "T-011", title: "Vocabulário LLM", file: "T-011.html",
    desc: "Tipos, erros e o contrato LLMProvider.", status: "done" },
  { group: "LLM", id: "T-012", title: "OllamaProvider", file: "T-012.html",
    desc: "Provider local via HTTP.", status: "done" },
  { group: "LLM", id: "T-013", title: "GroqProvider", file: "T-013.html",
    desc: "Provider em nuvem via SDK.", status: "done" },
  { group: "LLM", id: "T-014", title: "Streaming", file: "T-014.html",
    desc: "Tokens em tempo real.", status: "done" },
  { group: "LLM", id: "T-015", title: "LLMRouter", file: "T-015.html", desc: "Failover DeepInfra → Groq → Ollama.", status: "done" },
  { group: "LLM", id: "T-016", title: "DeepInfraProvider", file: "T-016.html", desc: "Provider principal na nuvem.", status: "done" },
  { group: "Tools", id: "T-020", title: "Base de Tools", file: "T-020.html", desc: "Tool, Tier, ToolResult.", status: "done" },
  { group: "Tools", id: "T-021", title: "Registry", file: "T-021.html", desc: "Auto-descoberta de tools.", status: "done" },
  { group: "Tools", id: "T-022", title: "Agenda Google", file: "T-022.html", desc: "Ler e marcar compromissos.", status: "done" },
  { group: "Tools", id: "T-026", title: "Comandos git", file: "T-026.html", desc: "search e deploy: terminal e agente (T-041, T-042).", status: "done" },
  { group: "Agente", id: "T-030", title: "Agent loop", file: "T-030.html", desc: "Decide → chama tool → reavalia.", status: "done" },
  { group: "Agente", id: "T-031", title: "Logging", file: "T-031.html", desc: "Eventos JSONL com segredos mascarados.", status: "done" },
  { group: "Clientes", id: "T-040", title: "CLI", file: "T-040.html", desc: "juniper chat completo.", status: "done" },
  { group: "Ferramentas", id: "Status", title: "Painel de status", file: "status.html",
    desc: "Testa até onde a Juniper está funcional.", status: "tool" },
];

(function () {
  const page = location.pathname.split("/").pop() || "index.html";
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  window.JUNIPER_FEATURES = FEATURES;

  const groups = [...new Set(FEATURES.map((f) => f.group))];
  let nav = `<a class="brand" href="index.html"><img class="logo" src="assets/logo.jpg" alt="" width="64" height="64"><span>Juniper · Livro de bolso</span></a>
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

  // Botão de tema: automático (sistema) → escuro → claro
  const label = { null: "◐ Tema: automático", dark: "☾ Tema: escuro", light: "☀ Tema: claro" };
  const btn = document.createElement("button");
  btn.className = "themebtn";
  btn.type = "button";
  const refresh = () => { btn.textContent = label[window.JuniperTheme ? window.JuniperTheme.get() : null]; };
  btn.addEventListener("click", () => { if (window.JuniperTheme) { window.JuniperTheme.cycle(); refresh(); } });
  refresh();
  side.append(btn);
  main.parentNode.insertBefore(shell, main);
  shell.append(side, main);

  // Cards do índice (se a página tiver o contêiner)
  const holder = document.getElementById("feature-cards");
  if (holder) {
    holder.innerHTML = FEATURES.map((f) => {
      const tag = { done: `<span class="tag g">pronta</span>`, tool: `<span class="tag v">ferramenta</span>`, guide: `<span class="tag v">guia</span>` }[f.status]
        || `<span class="tag a">planejada</span>`;
      const body = `${tag} <span class="tag b">${f.id}</span><h3>${esc(f.title)}</h3><p>${esc(f.desc)}</p>`;
      return f.file ? `<a class="fcard" href="${f.file}">${body}</a>` : `<div class="fcard soon">${body}</div>`;
    }).join("");
  }
})();
