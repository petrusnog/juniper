/* Tema claro/escuro. Carregado no <head> para aplicar a escolha antes da página pintar.
   Valores: "light" | "dark" | ausente = segue o sistema. */
(function () {
  const KEY = "juniper-theme";
  const read = () => { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
  const write = (v) => { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) {} };
  const apply = (v) => {
    if (v === "light" || v === "dark") document.documentElement.dataset.theme = v;
    else delete document.documentElement.dataset.theme;
  };
  const order = [null, "dark", "light"];
  const current = () => (["light", "dark"].includes(read()) ? read() : null);

  apply(current());
  window.JuniperTheme = {
    get: current,
    cycle() {
      const next = order[(order.indexOf(current()) + 1) % order.length];
      write(next);
      apply(next);
      return next;
    },
  };
})();
