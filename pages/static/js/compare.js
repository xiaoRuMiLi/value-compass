(function () {
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? "-"; return n; }
  function fmtYi(v) { return v == null ? "-" : `${(v / 1e8).toFixed(0)} 亿`; }
  function fmt(v) { return v == null ? "-" : v.toFixed(2); }

  async function render() {
    const rows = await getJSON("/api/screener");
    const table = document.getElementById("table");
    const hr = document.createElement("tr");
    ["股票", "市值", "ROE%", "毛利率%", "净利率%", "负债率%", "营收增速%"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const r of rows) {
      const tr = document.createElement("tr");
      [r.name, fmtYi(r.market_cap), fmt(r.roe), fmt(r.gross_margin), fmt(r.net_margin), fmt(r.debt_ratio), fmt(r.revenue_yoy)].forEach((t) => tr.appendChild(el("td", t)));
      table.appendChild(tr);
    }
  }
  render().catch(console.error);
})();
