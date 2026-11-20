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

  const PARAM_LABELS = {
    growth: "增长率",
    discount: "折现率",
    years: "预测年数",
    terminal_growth: "永续增长率",
    median_pe: "历史PE中位数",
    pe_percentile: "当前PE分位",
    eps: "EPS",
    bps: "BPS",
    dividend_per_share: "每股股利",
    fcf: "自由现金流",
    shares: "总股本",
  };

  function fmtParamValue(key, value) {
    if (value == null) return "数据缺失";
    if (["growth", "discount", "terminal_growth", "pe_percentile"].includes(key)) {
      return `${(value * 100).toFixed(1)}%`;
    }
    if (key === "fcf") return `${(value / 1e8).toFixed(2)}亿`;
    if (key === "shares") return `${(value / 1e8).toFixed(2)}亿股`;
    if (key === "median_pe" || key === "current_pe") return value.toFixed(2);
    return value;
  }

  function renderParams(item) {
    const parts = [];
    for (const [key, value] of Object.entries(item.assumptions || {})) {
      if (key === "formula") continue;
      parts.push(`${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`);
    }
    for (const [key, value] of Object.entries(item.inputs || {})) {
      parts.push(`${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`);
    }
    return parts.join("；");
  }

  function renderScore(score) {
    const box = document.getElementById("dimensions");
    for (const dimension of score.dimensions) {
      const block = el("div", "dim-block");
      const head = el("div", "dim-head");
      head.appendChild(el("span", "dim-label", dimension.dimension));
      head.appendChild(el("span", "dim-value", `${dimension.score}/${dimension.weight}`));
      const bar = el("div", "dim-bar");
      const fill = el("div", "dim-fill");
      fill.style.width = `${Math.min(dimension.score, 100)}%`;
      bar.appendChild(fill);
      block.appendChild(head);
      block.appendChild(bar);
      if (dimension.detail) {
        block.appendChild(el("div", "dim-detail", dimension.detail));
      }
      box.appendChild(block);
    }
  }

  function openValuationModal(method) {
    const meta = window.VALUATION_META && window.VALUATION_META[method];
    if (!meta) return;
    document.getElementById("valuationModalTitle").textContent = meta.name;
    const body = document.getElementById("valuationModalBody");
    body.innerHTML = "";
    const addBlock = (title, content) => {
      body.appendChild(el("h4", "modal-h", title));
      const list = el("ul", "modal-list");
      (Array.isArray(content) ? content : [content]).forEach((text) =>
        list.appendChild(el("li", null, text))
      );
      body.appendChild(list);
    };
    addBlock("历史由来", meta.history);
    addBlock("计算方法", meta.description);
    addBlock("优点", meta.pros);
    addBlock("缺点", meta.cons);
    addBlock("本项目细节", meta.details);
    const modal = document.getElementById("valuationModal");
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeValuationModal() {
    const modal = document.getElementById("valuationModal");
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }

  function renderValuation(valuation) {
    const box = document.getElementById("valuation");
    const names = { relative: "相对估值", graham: "格雷厄姆", ddm: "股利折现", dcf: "现金流折现" };
    for (const item of valuation) {
      const block = el("div", "val-block");
      block.id = "val-" + item.method;
      const row = el("div", "val-row");
      const nameCell = el("div", "val-name");
      nameCell.appendChild(el("span", null, names[item.method] || item.method));
      const help = el("button", "val-help", "?");
      help.type = "button";
      help.title = "查看该估值模型的历史与说明";
      help.setAttribute("aria-label", "查看估值模型说明");
      help.addEventListener("click", () => openValuationModal(item.method));
      nameCell.appendChild(help);
      const range = el(
        "div",
        "val-range",
        item.fair_value_low == null ? "暂无数据" : `${item.fair_value_low} ~ ${item.fair_value_high}`
      );
      const margin = el("div", "val-margin", `安全边际 ${fmtPct(item.margin_of_safety)}`);
      row.appendChild(nameCell);
      row.appendChild(range);
      row.appendChild(margin);
      block.appendChild(row);
      if (item.assumptions && item.assumptions.formula) {
        block.appendChild(el("div", "val-formula", item.assumptions.formula));
      }
      const params = renderParams(item);
      if (params) {
        block.appendChild(el("div", "val-params", params));
      }
      box.appendChild(block);
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

  function loadNarrative() {
    const box = document.getElementById("overviewNarrative");
    const sources = document.getElementById("narrativeSources");
    getJSON(`/api/stock/${symbol}/overview-narrative`)
      .then((data) => {
        box.innerHTML = "";
        for (const sec of data.sections || []) {
          const section = document.createElement("div");
          section.className = "narrative-section";
          section.appendChild(el("h3", null, sec.title));
          section.appendChild(el("p", null, sec.content));
          box.appendChild(section);
        }
        sources.textContent =
          data.sources && data.sources.length ? "数据来源：" + data.sources.join("、") : "";
      })
      .catch(() => {
        box.innerHTML = "";
        box.appendChild(el("p", "muted", "概览叙述生成失败"));
      });
  }

  function loadScoreNarrative() {
    const node = document.getElementById("scoreNarrative");
    getJSON(`/api/stock/${symbol}/score-narrative`)
      .then((data) => {
        node.textContent = data.text || "暂无解读";
      })
      .catch(() => {
        node.textContent = "解读生成失败";
      });
  }

  function loadValuationNarrative() {
    const loading = document.getElementById("valuationLoading");
    getJSON(`/api/stock/${symbol}/valuation-narrative`)
      .then((data) => {
        for (const sec of data.sections || []) {
          if (sec.method === "overall") {
            document.getElementById("valuationOverall").textContent = sec.content;
          } else {
            const block = document.getElementById("val-" + sec.method);
            if (block) block.appendChild(el("div", "val-note", sec.content));
          }
        }
        loading.remove();
      })
      .catch(() => {
        loading.textContent = "解读生成失败";
      });
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

  loadNarrative();
  loadScoreNarrative();
  loadValuationNarrative();
  document
    .getElementById("valuationModalClose")
    .addEventListener("click", closeValuationModal);
  document.querySelector(".modal-backdrop").addEventListener("click", closeValuationModal);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeValuationModal();
  });
  render().catch((error) => {
    console.error(error);
  });
})();
