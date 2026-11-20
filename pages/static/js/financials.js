(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";

  const { getJSON } = window.VC;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fmt(value) {
    if (value == null) return "-";
    if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
    return value.toFixed(2);
  }

  function pivot(rows) {
    const periods = [...new Set(rows.map((r) => r.report_date))].sort().reverse().slice(0, 8);
    const items = [...new Set(rows.map((r) => r.item_name))];
    const lookup = {};
    for (const row of rows) {
      (lookup[row.item_name] ||= {})[row.report_date] = row.item_value;
    }
    return { periods, items, lookup };
  }

  async function renderStatement(type) {
    const rows = await getJSON(`/api/stock/${symbol}/statement?type=${type}`);
    const { periods, items, lookup } = pivot(rows);
    const table = el("table");
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    headerRow.appendChild(el("th", null, "项目"));
    periods.forEach((p) => headerRow.appendChild(el("th", null, p)));
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (const item of items) {
      const row = document.createElement("tr");
      row.appendChild(el("td", null, item));
      periods.forEach((p) => row.appendChild(el("td", null, fmt(lookup[item]?.[p]))));
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    const box = document.getElementById("statement");
    box.innerHTML = "";
    box.appendChild(table);
  }

  async function renderComposition() {
    const rows = await getJSON(`/api/stock/${symbol}/revenue-composition`);
    const latest = rows[0]?.report_date;
    const latestRows = rows.filter((r) => r.report_date === latest);
    const box = document.getElementById("composition");
    const table = el("table");
    table.appendChild(el("tr"));
    const head = document.createElement("thead");
    const hr = document.createElement("tr");
    ["维度", "项目", "收入（亿）", "占比"].forEach((t) => hr.appendChild(el("th", null, t)));
    head.appendChild(hr);
    table.appendChild(head);
    const tbody = document.createElement("tbody");
    for (const row of latestRows) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, row.dimension === "region" ? "地区" : "产品"));
      tr.appendChild(el("td", null, row.item_name));
      tr.appendChild(el("td", null, row.revenue == null ? "-" : (row.revenue / 1e8).toFixed(2)));
      tr.appendChild(el("td", null, row.ratio == null ? "-" : `${row.ratio.toFixed(2)}%`));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    box.appendChild(table);
  }

  async function renderHolders() {
    const holders = await getJSON(`/api/stock/${symbol}/holders`);
    const holderTable = document.getElementById("holders");
    holderTable.innerHTML = "";
    const hr = document.createElement("tr");
    ["报告期", "排名", "股东名称", "性质", "持股数（股）", "占比（%）"].forEach((t) => hr.appendChild(el("th", null, t)));
    holderTable.appendChild(hr);
    for (const holder of holders.slice(0, 10)) {
      const tr = document.createElement("tr");
      [holder.report_date, holder.rank, holder.holder_name, holder.holder_category, holder.hold_num, holder.hold_ratio == null ? "-" : holder.hold_ratio.toFixed(2)].forEach((t) => tr.appendChild(el("td", null, t)));
      holderTable.appendChild(tr);
    }

    const counts = await getJSON(`/api/stock/${symbol}/shareholder-count`);
    const countTable = document.getElementById("holderCount");
    countTable.innerHTML = "";
    const chr = document.createElement("tr");
    ["报告期", "股东户数"].forEach((t) => chr.appendChild(el("th", null, t)));
    countTable.appendChild(chr);
    for (const count of counts) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, count.end_date));
      tr.appendChild(el("td", null, count.holder_count));
      countTable.appendChild(tr);
    }
  }

  async function render() {
    const tabs = document.querySelectorAll(".tab");
    tabs.forEach((tab) =>
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        renderStatement(tab.dataset.type);
      })
    );
    await Promise.all([renderStatement("income"), renderComposition(), renderHolders()]);
  }

  render().catch(console.error);
})();
