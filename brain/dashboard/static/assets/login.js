"use strict";
const form = document.getElementById("form");
const error = document.getElementById("error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  const clientId = document.getElementById("client-id").value.trim();
  const secret = document.getElementById("client-secret").value.trim();
  // The credential travels in a header, never in the URL (ADR-027).
  const bearer = secret ? `${clientId}.${secret}` : clientId;
  try {
    const response = await fetch("/api/login", { headers: { Authorization: `Bearer ${bearer}` } });
    if (response.ok) {
      window.location.href = "/";
      return;
    }
    const body = await response.json().catch(() => ({}));
    error.textContent = body.error || "Não consegui entrar.";
  } catch (_) {
    error.textContent = "Sem conexão com o servidor.";
  }
  error.hidden = false;
});
