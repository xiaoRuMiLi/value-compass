(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600019.SH";
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }

  function normalize(series) {
    const base = series[0]?.value || 1;
    return series.map((p) => ({ date: p.date, value: (p.value / base) * 100 }));
  }

  async function render() {
    const overview = await getJSON(`/api/stock/${symbol}/overview`);
    document.getElementById("title").textContent = `${overview.stock?.name || symbol} · 产品价格对比`;
    const products = await getJSON(`/api/stock/${symbol}/products`);
    if (!products.length) {
      document.getElementById("correlation").textContent = "未配置商品映射";
      return;
    }
    const data = await getJSON(`/api/stock/${symbol}/price-compare?product=${products[0].product_code}`);
    if (data.error) {
      document.getElementById("correlation").textContent = data.error;
      return;
    }
    const corr = data.correlation;
    document.getElementById("correlation").textContent =
      `商品 ${data.product_code} · 相关系数 ${corr.correlation ?? "-"} · Beta ${corr.beta ?? "-"}`;

    const price = normalize(data.price_series);
    const stock = normalize(data.stock_series);
    const chart = echarts.init(document.getElementById("chart"));
    chart.setOption({
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: ["商品价格", "股价"] },
      grid: { left: 56, right: 20, top: 40, bottom: 40 },
      xAxis: { type: "category", data: price.map((p) => p.date) },
      yAxis: { type: "value", name: "归一化(基期=100)" },
      series: [
        { name: "商品价格", type: "line", showSymbol: false, data: price.map((p) => p.value) },
        { name: "股价", type: "line", showSymbol: false, data: stock.map((p) => p.value) },
      ],
    });
  }
  render().catch(console.error);
})();
