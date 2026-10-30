// The pages of the dashboard, in menu order. Each module exports `render(rest) -> Node`.
export const pages = [
  { id: "visao", title: "Visão geral", module: "/assets/pages/overview.js" },
  { id: "memorias", title: "Memórias", module: "/assets/pages/memories.js" },
  { id: "central", title: "Memória central", module: "/assets/pages/central.js" },
  { id: "estudos", title: "Estudos", module: "/assets/pages/studies.js" },
  { id: "sonhos", title: "Sonhos", module: "/assets/pages/dreams.js" },
  { id: "conversas", title: "Conversas", module: "/assets/pages/conversations.js" },
  { id: "ocorrencias", title: "Ocorrências", module: "/assets/pages/occurrences.js" },
  { id: "pesquisa", title: "Pesquisa", module: "/assets/pages/search.js" },
  { id: "afetos", title: "Afetos", module: "/assets/pages/affects.js" },
  { id: "tarefas", title: "Tarefas", module: "/assets/pages/jobs.js" },
  { id: "logs", title: "Logs", module: "/assets/pages/logs.js" },
  { id: "saude", title: "Saúde e custos", module: "/assets/pages/health.js" },
  { id: "usuarios", title: "Usuários", module: "/assets/pages/users.js" },
];
