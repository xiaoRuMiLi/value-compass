(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? ""; return n; }

  async function render() {
    const events = await getJSON(`/api/stock/${symbol}/calendar`);
    const overview = await getJSON(`/api/stock/${symbol}/overview`);
    document.getElementById("title").textContent = `${overview.stock?.name || symbol} · 事件日历`;
    const table = document.getElementById("events");
    const hr = document.createElement("tr");
    ["日期", "类型", "事件"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const e of events) {
      const tr = document.createElement("tr");
      [e.date, e.type, e.title].forEach((t) => tr.appendChild(el("td", t)));
      table.appendChild(tr);
    }
  }
  render().catch(console.error);
})();
