(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  const { getJSON } = window.VC;
  const state = {
    frequency: "quarter",
    primaryKey: "net_profit",
    stmtType: "income",
    stmtKey: null,
    dashboard: null,
  };

  let dashChart = null;
  let stmtChart = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showError(id, error) {
    const box = document.getElementById(id);
    if (box) box.innerHTML = '<div class="fx-error">' + esc(error && error.message || error) + "</div>";
  }

  function scaled(value, scale) {
    return value == null ? null : Number(value) / (scale || 1);
  }

  function fmt(value, scale, unit) {
    const v = scaled(value, scale);
    if (v == null) return "-";
    const digits = Math.abs(v) >= 100 ? 1 : 2;
    return v.toFixed(digits) + (unit === "%" ? "%" : "");
  }

  function zoomStart(length) {
    return length > 24 ? Math.max(0, (1 - 24 / length) * 100) : 0;
  }

  function sparkline(values) {
    const points = values.filter((v) => v != null);
    if (points.length < 2) return "";
    const min = Math.min.apply(null, points);
    const max = Math.max.apply(null, points);
    const range = max - min || 1;
    const width = 120;
    const height = 24;
    const path = values.map((v, i) => {
      if (v == null) return null;
      const x = (i / Math.max(1, values.length - 1)) * width;
      const y = height - 2 - ((v - min) / range) * (height - 4);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).filter(Boolean).join(" ");
    return '<svg class="fx-ratio-spark" viewBox="0 0 ' + width + ' ' + height +
      '" preserveAspectRatio="none"><polyline fill="none" stroke="#3b82f6" stroke-width="1.5" points="' +
      path + '"></polyline></svg>';
  }

  function tooltip(labels, kline, bars, barName, barUnit, barScale) {
    return function (params) {
      const arr = Array.isArray(params) ? params : [params];
      const index = arr.length ? arr[0].dataIndex : null;
      if (index == null || !labels[index]) return "";
      const lines = ['<div style="font-weight:600;margin-bottom:4px">' + esc(labels[index]) + "</div>"];
      const bar = kline[index];
      if (bar) {
        lines.push('<div style="color:#64748b">报告期末交易日 ' + esc(bar.trade_date) +
          "　开 " + bar.open + " 收 " + bar.close + " 低 " + bar.low + " 高 " + bar.high + "</div>");
      }
      const value = scaled(bars[index], barScale);
      if (value != null) {
        lines.push('<div style="border-left:3px solid #3b82f6;padding-left:6px;margin-top:4px">' +
          esc(barName) + " " + value.toFixed(2) + " " + esc(barUnit) + "</div>");
      }
      return lines.join("");
    };
  }

  // 上方 K 线与下方柱形共用报告期 x 轴，缩放联动。
  function chartOption(labels, kline, barValues, barName, barUnit, barScale) {
    return {
      animation: false,
      grid: [
        { left: 64, right: 20, top: 28, height: "38%" },
        { left: 64, right: 20, top: "58%", height: "28%" },
      ],
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      xAxis: [
        { type: "category", data: labels, gridIndex: 0, boundaryGap: true,
          axisLabel: { show: false }, axisTick: { show: false } },
        { type: "category", data: labels, gridIndex: 1, boundaryGap: true,
          axisLabel: { color: "#94a3b8", fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, name: "股价",
          nameTextStyle: { color: "#94a3b8", fontSize: 10 },
          axisLabel: { color: "#94a3b8", fontSize: 10 }, splitLine: { show: false } },
        { scale: true, gridIndex: 1, name: barUnit,
          nameTextStyle: { color: "#64748b", fontSize: 10 },
          axisLabel: { color: "#64748b", fontSize: 10 }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: zoomStart(labels.length), end: 100 },
        { type: "slider", xAxisIndex: [0, 1], height: 18, bottom: 6,
          start: zoomStart(labels.length), end: 100,
          textStyle: { color: "#94a3b8", fontSize: 10 } },
      ],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }] },
        formatter: tooltip(labels, kline, barValues, barName, barUnit, barScale),
      },
      series: [
        {
          name: "报告期K线", type: "candlestick", xAxisIndex: 0, yAxisIndex: 0,
          // 缺失报告期用 '-' 占位：ECharts 遇到 null 配合 dataZoom 会在内部读 null.value 抛错，
          // 导致整个 setOption 失败、K 线消失。'-' 是官方的空数据占位符。
          data: kline.map((b) => b ? [b.open, b.close, b.low, b.high] : "-"),
          itemStyle: { color: "#dc2626", color0: "#16a34a", borderColor: "#dc2626", borderColor0: "#16a34a" },
        },
        {
          name: barName, type: "bar", xAxisIndex: 1, yAxisIndex: 1,
          data: barValues.map((v) => { const s = scaled(v, barScale); return s == null ? "-" : s; }),
          itemStyle: { color: "#3b82f6", borderRadius: [2, 2, 0, 0] },
        },
      ],
    };
  }

  function ensureChart(id, existing) {
    const dom = document.getElementById(id);
    // 自愈：容器若被外部清空（canvas 不在了），旧实例已与 DOM 脱钩，必须重建。
    if (existing && !dom.querySelector("canvas")) {
      try { existing.dispose(); } catch (error) { /* 忽略已销毁实例 */ }
      existing = null;
    }
    if (existing) return existing;
    const chart = echarts.init(dom);
    window.addEventListener("resize", function () { chart.resize(); });
    return chart;
  }

  function renderDashboard() {
    const data = state.dashboard;
    if (!data) return;
    const chips = document.getElementById("primaryChips");
    chips.innerHTML = "";
    for (const item of data.primary) {
      const button = el("button", "fx-chip" + (item.key === state.primaryKey ? " active" : ""),
        item.name);
      button.dataset.key = item.key;
      button.addEventListener("click", function () {
        state.primaryKey = item.key;
        renderDashboard();
      });
      chips.appendChild(button);
    }
    const metric = data.primary.find((m) => m.key === state.primaryKey) || data.primary[0];
    if (!metric) {
      showError("dashboardChart", "暂无主要指标数据");
      return;
    }
    dashChart = ensureChart("dashboardChart", dashChart);
    dashChart.setOption(chartOption(
      data.labels, data.kline, metric.values, metric.name, metric.unit, metric.scale
    ), true);
    document.getElementById("dashHint").textContent =
      "共 " + data.labels.length + " 期 · 当前：" + metric.name;
    document.getElementById("dashboardNote").textContent = (data.notes || []).join(" ");
  }

  function renderStatement() {
    const data = state.dashboard;
    if (!data) return;
    const items = data.statements[state.stmtType] || [];
    const select = document.getElementById("stmtMetric");
    select.innerHTML = "";
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.key;
      option.textContent = item.name + "（" + item.unit + "）";
      select.appendChild(option);
    }
    if (!items.length) {
      stmtChart = ensureChart("statementChart", stmtChart);
      stmtChart.clear();
      document.getElementById("statementTable").innerHTML =
        '<div class="fx-note">当前标的的该报表科目尚未入库。</div>';
      return;
    }
    if (!items.some((i) => i.key === state.stmtKey)) state.stmtKey = items[0].key;
    select.value = state.stmtKey;
    const metric = items.find((i) => i.key === state.stmtKey);
    stmtChart = ensureChart("statementChart", stmtChart);
    stmtChart.setOption(chartOption(
      data.labels, data.kline, metric.values, metric.name, metric.unit, metric.scale
    ), true);

    const table = el("table");
    const head = document.createElement("thead");
    const hr = document.createElement("tr");
    ["报告期", metric.name + "（" + metric.unit + "）"].forEach((t) => hr.appendChild(el("th", null, t)));
    head.appendChild(hr);
    table.appendChild(head);
    const tbody = document.createElement("tbody");
    for (let i = data.labels.length - 1; i >= Math.max(0, data.labels.length - 8); i--) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, data.labels[i]));
      tr.appendChild(el("td", null, fmt(metric.values[i], metric.scale, metric.unit)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    const box = document.getElementById("statementTable");
    box.innerHTML = "";
    box.appendChild(table);
  }

  function renderRatios() {
    const data = state.dashboard;
    if (!data) return;
    const box = document.getElementById("ratioGroups");
    box.innerHTML = "";
    if (!data.ratios || !Object.keys(data.ratios).length) {
      box.appendChild(el("div", "fx-note", "当前标的暂无可推导的财务比率。"));
      return;
    }
    for (const group of Object.values(data.ratios)) {
      const card = el("div", "fx-ratio-card");
      card.appendChild(el("h3", null, group.name));
      for (const item of group.items) {
        const row = el("div", "fx-ratio-row");
        row.title = item.formula;
        row.appendChild(el("span", "fx-ratio-name", item.name));
        const sparkHolder = el("span", null);
        sparkHolder.innerHTML = sparkline((item.values || []).slice(-12));
        row.appendChild(sparkHolder.firstChild || sparkHolder);
        const value = el("span", "fx-ratio-value", fmt(
          item.values[item.values.length - 1], item.scale, item.unit
        ));
        const unitLabel = el("small", null, item.unit);
        value.appendChild(unitLabel);
        row.appendChild(value);
        card.appendChild(row);
      }
      box.appendChild(card);
    }
  }

  async function loadDashboard() {
    // 不要清空图表容器：ECharts 的 canvas 是它的子节点，innerHTML 覆盖会让实例与 DOM 脱钩，
    // 之后 setOption 画到一个已经不在文档里的 canvas 上，表现为 K 线「加载不出来」。
    document.getElementById("dashHint").textContent = "加载中…";
    const data = await getJSON(
      "/api/stock/" + symbol + "/financial-dashboard?frequency=" + state.frequency
    );
    state.dashboard = data;
    renderDashboard();
    renderStatement();
    renderRatios();
  }

  async function loadNarrative(refresh) {
    const box = document.getElementById("narrativeBox");
    box.textContent = "生成中…";
    try {
      const result = await getJSON(
        "/api/stock/" + symbol + "/financial-trend-narrative" + (refresh ? "?refresh=true" : "")
      );
      box.innerHTML = String(result.text || "").split(/\n\s*\n/)
        .map((paragraph) => "<p>" + esc(paragraph) + "</p>").join("");
      document.getElementById("narrativeMeta").textContent =
        (result.source === "llm" ? "LLM 生成" : "模板回退") +
        (result.generated_at ? " · " + result.generated_at.slice(0, 16).replace("T", " ") : "");
    } catch (error) {
      showError("narrativeBox", error);
    }
  }

  async function renderComposition() {
    const rows = await getJSON("/api/stock/" + symbol + "/revenue-composition");
    const latest = rows[0] && rows[0].report_date;
    const table = el("table");
    const head = document.createElement("thead");
    const hr = document.createElement("tr");
    ["维度", "项目", "收入（亿）", "占比"].forEach((t) => hr.appendChild(el("th", null, t)));
    head.appendChild(hr);
    table.appendChild(head);
    const tbody = document.createElement("tbody");
    for (const row of rows.filter((r) => r.report_date === latest)) {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, row.dimension === "region" ? "地区" : "产品"));
      tr.appendChild(el("td", null, row.item_name));
      tr.appendChild(el("td", null, row.revenue == null ? "-" : (row.revenue / 1e8).toFixed(2)));
      tr.appendChild(el("td", null, row.ratio == null ? "-" : row.ratio.toFixed(2) + "%"));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    const box = document.getElementById("composition");
    box.innerHTML = "";
    box.appendChild(table);
  }

  async function renderHolders() {
    const holders = await getJSON("/api/stock/" + symbol + "/holders");
    const holderTable = document.getElementById("holders");
    holderTable.innerHTML = "";
    const hr = document.createElement("tr");
    ["报告期", "排名", "股东名称", "性质", "持股数（股）", "占比（%）"].forEach((t) =>
      hr.appendChild(el("th", null, t)));
    holderTable.appendChild(hr);
    for (const holder of holders.slice(0, 10)) {
      const tr = document.createElement("tr");
      [holder.report_date, holder.rank, holder.holder_name, holder.holder_category,
        holder.hold_num, holder.hold_ratio == null ? "-" : holder.hold_ratio.toFixed(2)]
        .forEach((t) => tr.appendChild(el("td", null, t)));
      holderTable.appendChild(tr);
    }

    const counts = await getJSON("/api/stock/" + symbol + "/shareholder-count");
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

  function bindControls() {
    document.querySelectorAll("#freqSwitch button").forEach((button) => {
      button.addEventListener("click", function () {
        document.querySelectorAll("#freqSwitch button").forEach((b) => b.classList.remove("active"));
        button.classList.add("active");
        state.frequency = button.dataset.freq;
        state.stmtKey = null;
        loadDashboard().catch((error) => showError("dashboardChart", error));
      });
    });
    document.querySelectorAll("#stmtTabs button").forEach((button) => {
      button.addEventListener("click", function () {
        document.querySelectorAll("#stmtTabs button").forEach((b) => b.classList.remove("active"));
        button.classList.add("active");
        state.stmtType = button.dataset.type;
        state.stmtKey = null;
        renderStatement();
      });
    });
    document.getElementById("stmtMetric").addEventListener("change", function (event) {
      state.stmtKey = event.target.value;
      renderStatement();
    });
    document.getElementById("refreshNarrative").addEventListener("click", function () {
      loadNarrative(true);
    });
  }

  bindControls();
  loadDashboard().catch((error) => showError("dashboardChart", error));
  loadNarrative(false);
  renderComposition().catch((error) => showError("composition", error));
  renderHolders().catch((error) => showError("holders", error));
})();
