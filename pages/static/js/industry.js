(function () {
  const { getJSON } = window.VC;

  function el(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text ?? "";
    return node;
  }

  function renderChart(industries) {
    const top = industries.slice(0, 60);
    const chart = echarts.init(document.getElementById("industryChart"));
    chart.setOption({
      animation: false,
      grid: { left: 60, right: 20, top: 20, bottom: 50 },
      tooltip: { formatter: (p) => `${p.data[3]}<br>涨跌 ${p.data[1]}%<br>主力净流入 ${(p.data[2] / 1e8).toFixed(2)} 亿` },
      xAxis: { name: "主力净流入（亿）", type: "value" },
      yAxis: { name: "涨跌幅（%）", type: "value" },
      series: [
        {
          type: "scatter",
          symbolSize: 10,
          data: top.map((i) => [i.capital_flow / 1e8, i.pct_change, i.leader_stock, i.industry_name]),
        },
      ],
    });
  }

  function renderTable(industries) {
    const table = document.getElementById("industryTable");
    const hr = document.createElement("tr");
    ["行业", "涨跌幅（%）", "主力净流入（亿）", "领涨股"].forEach((t) => hr.appendChild(el("th", t)));
    table.appendChild(hr);
    for (const industry of industries.slice(0, 200)) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", industry.industry_name));
      tr.appendChild(el("td", industry.pct_change == null ? "-" : industry.pct_change.toFixed(2)));
      tr.appendChild(el("td", industry.capital_flow == null ? "-" : (industry.capital_flow / 1e8).toFixed(2)));
      tr.appendChild(el("td", industry.leader_stock || "-"));
      table.appendChild(tr);
    }
  }

  async function render() {
    const industries = await getJSON("/api/industry");
    renderChart(industries);
    renderTable(industries);
  }

  render().catch(console.error);
})();
