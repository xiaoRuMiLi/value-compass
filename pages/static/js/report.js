(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";

  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${url}`);
    return response.json();
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function fmtPct(value) {
    return value == null ? "-" : `${(value * 100).toFixed(2)}%`;
  }

  function renderScore(score) {
    const box = document.getElementById("dimensions");
    for (const dimension of score.dimensions) {
      const row = el("div", "dim-row");
      const label = el("span", "dim-label", dimension.dimension);
      const bar = el("div", "dim-bar");
      const fill = el("div", "dim-fill");
      fill.style.width = `${Math.min(dimension.score, 100)}%`;
      bar.appendChild(fill);
      const value = el("span", "dim-value", `${dimension.score}/${dimension.weight}`);
      row.appendChild(label);
      row.appendChild(bar);
      row.appendChild(value);
      box.appendChild(row);
    }
  }

  function renderValuation(valuation) {
    const box = document.getElementById("valuation");
    const names = { relative: "相对估值", graham: "格雷厄姆", ddm: "股利折现", dcf: "现金流折现" };
    for (const item of valuation) {
      const row = el("div", "val-row");
      const name = el("div", "val-name", names[item.method] || item.method);
      const range = el(
        "div",
        "val-range",
        item.fair_value_low == null ? "暂无数据" : `${item.fair_value_low} ~ ${item.fair_value_high}`
      );
      const margin = el("div", "val-margin", `安全边际 ${fmtPct(item.margin_of_safety)}`);
      row.appendChild(name);
      row.appendChild(range);
      row.appendChild(margin);
      box.appendChild(row);
    }
  }

  function renderMasters(masters) {
    const box = document.getElementById("masters");
    const labels = { buffett: "巴菲特", duan_yongping: "段永平" };
    for (const master of masters) {
      const card = el("div", "master-card");
      card.appendChild(el("div", "master-head", `${labels[master.master] || master.master}：${master.verdict}（${master.score}）`));
      for (const signal of master.signals) {
        card.appendChild(el("div", "signal", `${signal.passed ? "✓" : "✗"} ${signal.name} — ${signal.detail || ""}`));
      }
      box.appendChild(card);
    }
  }

  function renderReview(review, moat, cycle, fraud, delisting) {
    document.getElementById("reviewSummary").textContent = review.summary;
    const findings = document.getElementById("findings");
    for (const finding of review.findings) {
      findings.appendChild(el("li", "finding", `[${finding.type}] ${finding.detail}`));
    }
    const tags = document.getElementById("tags");
    tags.appendChild(el("span", "tag", `护城河：${moat.rating}`));
    tags.appendChild(el("span", "tag", `周期：${cycle.position}`));
    if (fraud.score != null) tags.appendChild(el("span", "tag", `造假风险：${fraud.score}`));
    if (delisting.score != null) tags.appendChild(el("span", "tag", `退市风险：${delisting.score}`));
  }

  function renderKline(kline) {
    const chart = echarts.init(document.getElementById("kline"));
    chart.setOption({
      animation: false,
      grid: { left: 56, right: 20, top: 20, bottom: 44 },
      xAxis: { type: "category", data: kline.map((b) => b.trade_date) },
      yAxis: { scale: true },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20, bottom: 8 }],
      tooltip: { trigger: "axis" },
      series: [{ type: "candlestick", data: kline.map((b) => [b.open, b.close, b.low, b.high]) }],
    });
  }

  function renderFinancials(financials) {
    const tbody = document.querySelector("#financials tbody");
    for (const item of financials.slice(0, 12)) {
      const row = document.createElement("tr");
      [
        item.report_date,
        (item.revenue / 1e8).toFixed(2),
        (item.net_profit / 1e8).toFixed(2),
        item.roe == null ? "-" : item.roe.toFixed(2),
      ].forEach((text) => row.appendChild(el("td", null, text)));
      tbody.appendChild(row);
    }
  }

  async function render() {
    const [analysis, kline, financials] = await Promise.all([
      getJSON(`/api/stock/${symbol}/analysis`),
      getJSON(`/api/stock/${symbol}/kline`),
      getJSON(`/api/stock/${symbol}/financials`),
    ]);
    renderScore(analysis.score);
    renderValuation(analysis.valuation);
    renderMasters(analysis.masters);
    renderReview(analysis.review, analysis.moat, analysis.cycle, analysis.fraud, analysis.delisting);
    renderKline(kline);
    renderFinancials(financials);
  }

  render().catch((error) => {
    console.error(error);
  });
})();
