/* Busca no livro de bolso. Funciona sem servidor (abre direto do disco) e sem internet.
   Índice: window.JUNIPER_SEARCH (assets/docs-data.js), uma entrada por seção:
     p = página, t = título da página, g = grupo, h = título da seção, a = âncora,
     x = texto da seção, k = tipo (se for uma entidade), w = palavras-chave da página.
   Atalhos: Ctrl+K (ou ⌘K) e "/" abrem a busca; ↑ ↓ navegam; Enter abre; Esc fecha. */
(function () {
  const DATA = window.JUNIPER_SEARCH || [];

  // Tira os acentos sem mudar o tamanho do texto (as posições continuam valendo no original).
  const fold = (s) => String(s).replace(/[^\u0000-\u007f]/g, (ch) => ch.normalize("NFD")[0]).toLowerCase();
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const docs = DATA.map((d) => ({
    d,
    nh: fold(d.h),
    nt: fold(d.t),
    nx: fold(d.x),
    nw: fold(d.w || ""),
    nk: fold(d.k || ""),
  }));

  // Posição da primeira ocorrência de t em hay que começa uma "palavra" (ou -1).
  function boundary(hay, t) {
    let i = hay.indexOf(t);
    while (i !== -1) {
      if (i === 0 || !/[a-z0-9]/.test(hay[i - 1])) return i;
      i = hay.indexOf(t, i + 1);
    }
    return -1;
  }

  // Pontuação de uma seção para os termos; 0 = não serve (todos os termos precisam aparecer).
  function score(doc, terms) {
    let total = 0;
    for (const t of terms) {
      let s = 0;
      if (doc.nh === t) s += 30;
      else {
        const b = boundary(doc.nh, t);
        if (b === 0) s += 14;
        else if (b > 0) s += 9;
        else if (doc.nh.includes(t)) s += 5;
      }
      if (doc.nw && boundary(doc.nw, t) >= 0) s += 6;
      if (boundary(doc.nt, t) >= 0) s += 3;
      if (doc.nk && doc.nk.includes(t)) s += 2;
      if (boundary(doc.nx, t) >= 0) s += 2;
      else if (t.length >= 3 && doc.nx.includes(t)) s += 1;
      if (s === 0) return 0;
      total += s;
    }
    return doc.d.k ? total + 2 : total;
  }

  function search(query, limit) {
    const terms = fold(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    const out = [];
    for (const doc of docs) {
      const s = score(doc, terms);
      if (s > 0) out.push({ doc, s });
    }
    out.sort((a, b) => b.s - a.s || a.doc.d.t.localeCompare(b.doc.d.t));
    return out.slice(0, limit).map((r) => ({ ...r, terms }));
  }

  // Trecho do texto em volta do primeiro termo achado, com os termos em destaque.
  function snippet(doc, terms) {
    const text = doc.d.x;
    if (!text) return "";
    let at = -1;
    for (const t of terms) {
      const b = boundary(doc.nx, t);
      const i = b >= 0 ? b : doc.nx.indexOf(t);
      if (i >= 0 && (at < 0 || i < at)) at = i;
    }
    const start = Math.max(0, at < 0 ? 0 : at - 50);
    const end = Math.min(text.length, start + 150);
    const piece = text.slice(start, end);
    const folded = fold(piece);
    const marks = new Array(piece.length).fill(false);
    for (const t of terms) {
      let i = folded.indexOf(t);
      while (i !== -1) {
        for (let j = i; j < i + t.length; j++) marks[j] = true;
        i = folded.indexOf(t, i + t.length);
      }
    }
    let html = "";
    for (let i = 0; i < piece.length; ) {
      const on = marks[i];
      let j = i;
      while (j < piece.length && marks[j] === on) j++;
      const chunk = esc(piece.slice(i, j));
      html += on ? `<mark>${chunk}</mark>` : chunk;
      i = j;
    }
    return (start > 0 ? "…" : "") + html + (end < text.length ? "…" : "");
  }

  function highlight(text, terms) {
    const folded = fold(text);
    const marks = new Array(text.length).fill(false);
    for (const t of terms) {
      let i = folded.indexOf(t);
      while (i !== -1) {
        for (let j = i; j < i + t.length; j++) marks[j] = true;
        i = folded.indexOf(t, i + t.length);
      }
    }
    let html = "";
    for (let i = 0; i < text.length; ) {
      const on = marks[i];
      let j = i;
      while (j < text.length && marks[j] === on) j++;
      const chunk = esc(text.slice(i, j));
      html += on ? `<mark>${chunk}</mark>` : chunk;
      i = j;
    }
    return html;
  }

  // ---- interface -------------------------------------------------------

  const overlay = document.createElement("div");
  overlay.className = "search-overlay";
  overlay.hidden = true;
  overlay.innerHTML = `<div class="search-box" role="dialog" aria-modal="true" aria-label="Buscar na documentação">
      <input type="search" autocomplete="off" spellcheck="false"
        placeholder="Buscar… ex.: Dispatcher, retrieval.threshold, deploy" aria-label="Termos da busca">
      <ul class="results" role="listbox"></ul>
      <div class="hint"><span>↑ ↓ navegar</span><span>Enter abrir</span><span>Esc fechar</span></div>
    </div>`;
  document.body.append(overlay);
  const input = overlay.querySelector("input");
  const list = overlay.querySelector(".results");
  let results = [];
  let selected = 0;
  let lastFocus = null;

  function render() {
    const q = input.value.trim();
    if (!q) {
      list.innerHTML = `<li class="empty">Digite para buscar em ${new Set(DATA.map((d) => d.p)).size} páginas: nomes de classes, tools, opções do <code>config.yaml</code>, comandos…</li>`;
      results = [];
      return;
    }
    results = search(q, 20);
    selected = 0;
    if (!results.length) {
      list.innerHTML = `<li class="empty">Nada encontrado para “${esc(q)}”. Tente menos palavras.</li>`;
      return;
    }
    list.innerHTML = results.map((r, i) => {
      const d = r.doc.d;
      const where = d.h === d.t ? esc(d.g) : `${esc(d.g)} › ${esc(d.t)}`;
      const kind = d.k ? ` <span class="tag b">${esc(d.k)}</span>` : "";
      return `<li role="option" class="${i === 0 ? "sel" : ""}" data-i="${i}"><a href="${d.p}${d.a ? "#" + d.a : ""}">
        <div class="rt">${highlight(d.h, r.terms)}${kind}</div>
        <div class="rp">${where}</div>
        <div class="rs">${snippet(r.doc, r.terms)}</div></a></li>`;
    }).join("");
  }

  function select(i) {
    const items = list.querySelectorAll("li[data-i]");
    if (!items.length) return;
    selected = (i + items.length) % items.length;
    items.forEach((el, n) => el.classList.toggle("sel", n === selected));
    items[selected].scrollIntoView({ block: "nearest" });
  }

  function open() {
    lastFocus = document.activeElement;
    overlay.hidden = false;
    document.body.classList.add("searching");
    input.value = "";
    render();
    input.focus();
  }

  function close() {
    overlay.hidden = true;
    document.body.classList.remove("searching");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function go() {
    const r = results[selected];
    if (!r) return;
    const d = r.doc.d;
    const url = d.p + (d.a ? "#" + d.a : "");
    const samePage = location.pathname.split("/").pop() === d.p || (d.p === "index.html" && !location.pathname.split("/").pop());
    close();
    if (samePage && d.a) { location.hash = d.a; } else { location.href = url; }
  }

  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); select(selected + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); select(selected - 1); }
    else if (e.key === "Enter") { e.preventDefault(); go(); }
  });
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
  list.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-i]");
    if (!li) return;
    e.preventDefault();
    selected = Number(li.dataset.i);
    go();
  });
  list.addEventListener("mousemove", (e) => {
    const li = e.target.closest("li[data-i]");
    if (li && Number(li.dataset.i) !== selected) select(Number(li.dataset.i));
  });

  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || "");
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); overlay.hidden ? open() : close(); }
    else if (e.key === "/" && !typing && overlay.hidden) { e.preventDefault(); open(); }
    else if (e.key === "Escape" && !overlay.hidden) { e.preventDefault(); close(); }
  });
  document.querySelectorAll("[data-search]").forEach((el) => el.addEventListener("click", open));

  // Filtro da página de entidades
  const filter = document.getElementById("entity-filter");
  const table = document.getElementById("entity-table");
  if (filter && table) {
    const rows = [...table.querySelectorAll("tbody tr")];
    filter.addEventListener("input", () => {
      const terms = fold(filter.value).split(/\s+/).filter(Boolean);
      for (const row of rows) {
        const text = fold(row.textContent);
        row.hidden = !terms.every((t) => text.includes(t));
      }
    });
  }

  // API para testes e para quem quiser reaproveitar
  window.JuniperSearch = { search, open, close };
})();
