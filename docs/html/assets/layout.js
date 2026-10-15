/* Layout compartilhado: menu lateral, índice da página ("Nesta página") e botões de copiar.
   O menu vem de assets/docs-data.js (window.JUNIPER_NAV), gerado por scripts/build_docs.py a
   partir do cabeçalho de cada página em docs/pages/*.md. Para registrar uma página nova, crie o
   .md com group/order/title/description e rode `python scripts/build_docs.py`. */
(function () {
  const NAV = window.JUNIPER_NAV || [];
  const page = location.pathname.split("/").pop() || "index.html";
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const isActive = (file) => page === file || (file === "adr.html" && /^adr-\d+\.html$/.test(page));

  window.JUNIPER_FEATURES = NAV; // compatibilidade com páginas antigas

  const groups = [...new Set(NAV.map((f) => f.group))];
  let nav = `<a class="brand" href="index.html"><img class="logo" src="assets/juniper.png" alt="" width="64" height="64"><span>Juniper · Livro de bolso</span></a>
    <div class="sub">Documentação técnica</div>
    <button class="searchbtn" type="button" data-search aria-label="Buscar na documentação"><span>🔍 Buscar</span><kbd>Ctrl K</kbd></button>
    <a class="item ${page === "index.html" ? "active" : ""}" href="index.html">Início</a>`;
  for (const g of groups) {
    nav += `<h4>${esc(g)}</h4>`;
    for (const f of NAV.filter((x) => x.group === g)) {
      nav += `<a class="item ${isActive(f.file) ? "active" : ""}" href="${f.file}">${esc(f.title)}</a>`;
    }
  }

  const main = document.getElementById("content");
  const shell = document.createElement("div");
  shell.className = "shell";
  const side = document.createElement("nav");
  side.className = "side";
  side.setAttribute("aria-label", "Menu da documentação");
  side.innerHTML = nav;

  // Em telas estreitas o menu começa recolhido (são muitas páginas)
  const menuBtn = document.createElement("button");
  menuBtn.type = "button";
  menuBtn.className = "menubtn";
  menuBtn.textContent = "☰ Menu";
  menuBtn.setAttribute("aria-expanded", "false");
  menuBtn.addEventListener("click", () => {
    const open = side.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  side.querySelector(".brand").after(menuBtn);

  // Botão de tema: automático (sistema) → escuro → claro
  const label = { null: "◐ Tema: automático", dark: "☾ Tema: escuro", light: "☀ Tema: claro" };
  const btn = document.createElement("button");
  btn.className = "themebtn";
  btn.type = "button";
  const refresh = () => { btn.textContent = label[window.JuniperTheme ? window.JuniperTheme.get() : null]; };
  btn.addEventListener("click", () => { if (window.JuniperTheme) { window.JuniperTheme.cycle(); refresh(); } });
  refresh();
  side.append(btn);

  // Créditos
  const credit = document.createElement("div");
  credit.className = "credit";
  credit.textContent = "Criada por Petrus Rennan ☕";
  side.append(credit);
  main.parentNode.insertBefore(shell, main);
  shell.append(side, main);

  // Cards do índice (se a página tiver o contêiner)
  const holder = document.getElementById("feature-cards");
  if (holder) {
    const tagFor = {
      pronta: `<span class="tag g">pronta</span>`,
      guia: `<span class="tag v">guia</span>`,
      ferramenta: `<span class="tag v">ferramenta</span>`,
      proposto: `<span class="tag a">proposto</span>`,
      planejada: `<span class="tag a">planejada</span>`,
    };
    holder.outerHTML = groups.map((g) => {
      const cards = NAV.filter((f) => f.group === g).map((f) =>
        `<a class="fcard" href="${f.file}">${tagFor[f.status] || ""}<h3>${esc(f.title)}</h3><p>${esc(f.desc)}</p></a>`
      ).join("");
      return `<h2>${esc(g)}</h2><div class="cards">${cards}</div>`;
    }).join("");
  }

  // "Nesta página": índice das seções (só quando há o que navegar)
  const heads = [...main.querySelectorAll("h2[id], h3[id]")];
  if (heads.length >= 3) {
    const toc = document.createElement("aside");
    toc.className = "toc";
    toc.setAttribute("aria-label", "Nesta página");
    toc.innerHTML = `<h4>Nesta página</h4>` + heads.map((h) =>
      `<a class="t${h.tagName === "H3" ? "3" : "2"}" href="#${h.id}" data-id="${h.id}">${esc(h.textContent)}</a>`
    ).join("");
    shell.append(toc);
    if ("IntersectionObserver" in window) {
      const links = new Map([...toc.querySelectorAll("a")].map((a) => [a.dataset.id, a]));
      const seen = new Set();
      const io = new IntersectionObserver((entries) => {
        for (const e of entries) { if (e.isIntersecting) seen.add(e.target.id); else seen.delete(e.target.id); }
        const first = heads.find((h) => seen.has(h.id));
        links.forEach((a) => a.classList.remove("on"));
        if (first && links.get(first.id)) links.get(first.id).classList.add("on");
      }, { rootMargin: "0px 0px -70% 0px" });
      heads.forEach((h) => io.observe(h));
    }
  }

  // Botão de copiar nos blocos de código
  main.querySelectorAll("pre").forEach((pre) => {
    if (pre.closest(".cmd")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "copybtn";
    b.textContent = "Copiar";
    b.addEventListener("click", () => {
      const text = pre.innerText.replace(/\s+$/, "");
      const done = () => { b.textContent = "Copiado"; setTimeout(() => { b.textContent = "Copiar"; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, () => {});
    });
    pre.classList.add("hascopy");
    pre.append(b);
  });

  // Se a URL tem #secao, garante que o título não fique escondido atrás de nada
  if (location.hash) {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target) target.scrollIntoView();
  }
})();
