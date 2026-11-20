(function () {
  const { getJSON } = window.VC;
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? "-"; return n; }
  function fmtYi(v) { return v == null ? "-" : `${(v / 1e8).toFixed(0)} 亿`; }
  function fmt(v) { return v == null ? "-" : v.toFixed(2); }

  async function render(params) {
    const query = new URLSearchParams(params).toString();
    const rows = await getJSON(`/api/screener${query ? `?${query}` : ""}`);
    const table = document.getElementById("table");
    table.innerHTML = "";
    const hr = document.createElement("tr");
    ["股票", "市值", "ROE%", "毛利率%", "净利率%", "负债率%", "营收增速%"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const r of rows) {
      const tr = document.createElement("tr");
      [r.name, fmtYi(r.market_cap), fmt(r.roe), fmt(r.gross_margin), fmt(r.net_margin), fmt(r.debt_ratio), fmt(r.revenue_yoy)].forEach((t) => tr.appendChild(el("td", t)));
      table.appendChild(tr);
    }
  }

  render();
  document.getElementById("filter").addEventListener("click", () => {
    const params = {};
    const minRoe = document.getElementById("minRoe").value;
    const minGross = document.getElementById("minGross").value;
    const maxDebt = document.getElementById("maxDebt").value;
    const minGrowth = document.getElementById("minGrowth").value;
    if (minRoe) params.min_roe = minRoe;
    if (minGross) params.min_gross = minGross;
    if (maxDebt) params.max_debt = maxDebt;
    if (minGrowth) params.min_revenue_growth = minGrowth;
    render(params).catch(console.error);
  });
})();
