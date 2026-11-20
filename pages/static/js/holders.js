(function () {
  const { getJSON, normalize } = window.VC;

  function el(tag, text) {
    const n = document.createElement(tag);
    n.textContent = text ?? "-";
    return n;
  }

  function changeText(r) {
    if (r.is_new) return "新进";
    if (r.change_ratio == null) return "-";
    if (r.change_ratio > 0) return `+${r.change_ratio.toFixed(2)}%`;
    if (r.change_ratio < 0) return `${r.change_ratio.toFixed(2)}%`;
    return "不变";
  }

  const symbol = normalize(new URLSearchParams(location.search).get("symbol") || "600519");

  async function render() {
    const [rows, stocks] = await Promise.all([
      getJSON(`/api/stock/${encodeURIComponent(symbol)}/holders`).catch(() => []),
      getJSON("/api/stocks").catch(() => []),
    ]);
    const stock = stocks.find((s) => s.symbol === symbol);
    document.getElementById("title").textContent = stock
      ? `${stock.name} · 十大流通股东`
      : "十大流通股东";

    const table = document.getElementById("table");
    table.innerHTML = "";
    const hr = document.createElement("tr");
    ["报告期", "排名", "股东", "性质", "持股数（万股）", "占比%", "变动"].forEach((t) =>
      hr.appendChild(el("th", t))
    );
    table.appendChild(hr);
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", r.report_date));
      tr.appendChild(el("td", r.rank));
      tr.appendChild(el("td", r.holder_name));
      tr.appendChild(el("td", r.holder_category));
      tr.appendChild(el("td", r.hold_num == null ? "-" : (r.hold_num / 1e4).toFixed(0)));
      tr.appendChild(el("td", r.hold_ratio == null ? "-" : r.hold_ratio.toFixed(2)));
      tr.appendChild(el("td", changeText(r)));
      table.appendChild(tr);
    }
  }

  render().catch(console.error);
})();
