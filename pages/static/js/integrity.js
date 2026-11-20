(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  const { getJSON } = window.VC;
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? ""; return n; }

  async function render() {
    const rows = await getJSON(`/api/stock/${symbol}/integrity`);
    const table = document.getElementById("records");
    const hr = document.createElement("tr");
    ["日期", "性质", "类别", "严重度", "事件"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const r of rows) {
      const tr = document.createElement("tr");
      [r.record_date, r.behavior_type, r.category, r.severity, r.title].forEach((t) => tr.appendChild(el("td", t)));
      table.appendChild(tr);
    }
  }
  render().catch(console.error);
})();
