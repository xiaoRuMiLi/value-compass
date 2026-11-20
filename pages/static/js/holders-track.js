(function () {
  const { getJSON } = window.VC;
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? "-"; return n; }

  async function render(name) {
    const url = name ? `/api/holders/track?name=${encodeURIComponent(name)}` : "/api/holders/track";
    const rows = await getJSON(url);
    const table = document.getElementById("table");
    table.innerHTML = "";
    const hr = document.createElement("tr");
    ["报告期", "股东", "性质", "股票", "持股数（万股）", "占比%"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const r of rows) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", r.report_date));
      tr.appendChild(el("td", r.holder_name));
      tr.appendChild(el("td", r.holder_category));
      tr.appendChild(el("td", r.stock_name || r.code));
      tr.appendChild(el("td", r.hold_num == null ? "-" : (r.hold_num / 1e4).toFixed(0)));
      tr.appendChild(el("td", r.hold_ratio == null ? "-" : r.hold_ratio.toFixed(2)));
      table.appendChild(tr);
    }
  }

  render();
  document.getElementById("search").addEventListener("click", () => {
    render(document.getElementById("name").value).catch(console.error);
  });
})();
