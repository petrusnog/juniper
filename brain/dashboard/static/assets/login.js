"use strict";
const form = document.getElementById("form");
const error = document.getElementById("error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  error.hidden = true;
  const token = document.getElementById("token").value.trim();
  try {
    const response = await fetch("/api/login", { headers: { Authorization: `Bearer ${token}` } });
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
