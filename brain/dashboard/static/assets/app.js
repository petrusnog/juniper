"use strict";
const status = document.getElementById("status");

async function load() {
  const response = await fetch("/api/session");
  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  status.textContent = response.ok ? "Conectado." : "O servidor não respondeu direito.";
}

document.getElementById("logout").addEventListener("click", async () => {
  await fetch("/api/logout");
  window.location.href = "/login";
});

load().catch(() => {
  status.textContent = "Sem conexão com o servidor.";
});
