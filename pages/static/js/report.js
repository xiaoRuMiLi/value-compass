(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";

  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${url}`);
    return response.json();
  }

  async function render() {
    const [overview, financials, kline] = await Promise.all([
      getJSON(`/api/stock/${symbol}/overview`),
      getJSON(`/api/stock/${symbol}/financials`),
      getJSON(`/api/stock/${symbol}/kline`),
    ]);

    const stock = overview.stock || {};
    document.getElementById("title").textContent = `${stock.name || symbol}（${stock.code}.${stock.market}）`;
    document.getElementById("price").textContent = overview.snapshot?.price ?? "-";

    const chart = echarts.init(document.getElementById("kline"));
    chart.setOption({
      animation: false,
      grid: { left: 56, right: 20, top: 20, bottom: 44 },
      xAxis: { type: "category", data: kline.map((b) => b.trade_date) },
      yAxis: { scale: true },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20, bottom: 8 }],
      tooltip: { trigger: "axis" },
      series: [
        {
          type: "candlestick",
          data: kline.map((b) => [b.open, b.close, b.low, b.high]),
        },
      ],
    });

    const tbody = document.querySelector("#financials tbody");
    for (const item of financials.slice(0, 12)) {
      const row = document.createElement("tr");
      const cells = [
        item.report_date,
        (item.revenue / 1e8).toFixed(2),
        (item.net_profit / 1e8).toFixed(2),
        item.roe == null ? "-" : item.roe.toFixed(2),
      ];
      cells.forEach((text) => {
        const td = document.createElement("td");
        td.textContent = text;
        row.appendChild(td);
      });
      tbody.appendChild(row);
    }
  }

  render().catch((error) => {
    document.getElementById("title").textContent = "加载失败";
    console.error(error);
  });
})();
