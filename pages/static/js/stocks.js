(function () {
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? "-"; return n; }

  async function render() {
    const rows = await getJSON("/api/stocks");
    const table = document.getElementById("table");
    const hr = document.createElement("tr");
    ["代码", "名称", "现价", "涨跌幅"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.appendChild(el("td", r.code));
      tr.appendChild(el("td", r.name));
      tr.appendChild(el("td", r.price == null ? "-" : r.price.toFixed(2)));
      tr.appendChild(el("td", r.pct_change == null ? "-" : `${r.pct_change > 0 ? "+" : ""}${r.pct_change.toFixed(2)}%`));
      tr.addEventListener("click", () => { location.href = `/report?symbol=${encodeURIComponent(r.symbol)}`; });
      table.appendChild(tr);
    }
  }
  render().catch(console.error);
})();
