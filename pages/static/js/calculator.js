(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }
  function el(tag, className, text) { const n = document.createElement(tag); if (className) n.className = className; n.textContent = text ?? ""; return n; }
  function pct(v) { return v == null ? "-" : `${(v * 100).toFixed(2)}%`; }

  async function renderReturns(start, end) {
    const r = await getJSON(`/api/stock/${symbol}/return?start=${start}&end=${end}`);
    const box = document.getElementById("returns");
    box.innerHTML = "";
    [
      ["区间", `${r.start_date} ~ ${r.end_date}`],
      ["价格回报", pct(r.price_return)],
      ["分红回报", pct(r.dividend_return)],
      ["总回报", pct(r.total_return)],
      ["年化 CAGR", pct(r.cagr)],
      ["累计每股分红", r.cumulative_dividends == null ? "-" : `${r.cumulative_dividends.toFixed(2)} 元`],
    ].forEach(([k, v]) => {
      const row = el("div", "val-row");
      row.appendChild(el("div", "val-name", k));
      row.appendChild(el("div", "val-range", v));
      box.appendChild(row);
    });
  }

  async function render() {
    const analysis = await getJSON(`/api/stock/${symbol}/analysis`);
    const names = { relative: "相对估值", graham: "格雷厄姆", ddm: "股利折现", dcf: "现金流折现" };
    const box = document.getElementById("valuation");
    for (const v of analysis.valuation) {
      const row = el("div", "val-row");
      row.appendChild(el("div", "val-name", names[v.method] || v.method));
      row.appendChild(el("div", "val-range", v.fair_value_low == null ? "暂无" : `${v.fair_value_low} ~ ${v.fair_value_high}`));
      row.appendChild(el("div", "val-margin", `安全边际 ${pct(v.margin_of_safety)}`));
      box.appendChild(row);
    }
    const today = new Date().toISOString().slice(0, 10);
    const fiveYearsAgo = new Date(Date.now() - 5 * 365 * 86400000).toISOString().slice(0, 10);
    document.getElementById("start").value = fiveYearsAgo;
    document.getElementById("end").value = today;
    await renderReturns(fiveYearsAgo, today);
    document.getElementById("calc").addEventListener("click", () => {
      renderReturns(document.getElementById("start").value, document.getElementById("end").value).catch(console.error);
    });
  }
  render().catch(console.error);
})();
