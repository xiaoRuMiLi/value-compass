(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  const { getJSON } = window.VC;
  const state = {
    frequency: "quarter",
    primaryKey: "net_profit",
    stmtType: "income",
    stmtKey: null,
    dashboard: null,
    balanceData: null,
    fcfFrequency: "quarter",
    detailFreq: "quarter",
    detailPrimary: null,
    detailOverlay: [],
    explainIndex: {}, // 缓存索引：scope|stmt_type|科目组合|报告期 -> 摘要
  };

  let dashChart = null;
  let stmtChart = null;
  const DETAIL_COLORS = [
    "#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#ef4444", "#06b6d4", "#ec4899", "#84cc16",
  ];

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

  function indexKey(scope, stmtType, keys, period) {
    return [scope || "", stmtType || "", (keys || []).join(","), period || ""].join("|");
  }

  // 一次取回该标的全部已缓存解释，供各图表 tooltip 直接查表，不产生逐期请求。
  async function loadExplanationIndex() {
    try {
      const data = await getJSON("/api/stock/" + symbol + "/metric-explanation-index");
      for (const item of data.items || []) {
        state.explainIndex[indexKey(item.scope, item.stmt_type, item.keys, item.period)] = item;
      }
    } catch (error) {
      // 索引加载失败时仅失去悬停提示，不影响图表与手动生成
    }
  }

  function cachedHint(scope, stmtType, keys, period) {
    const hit = state.explainIndex[indexKey(scope, stmtType, keys, period)];
    return hit ? hit.summary : "";
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

  function tooltip(labels, kline, bars, barName, barUnit, barScale, hintFn) {
    return function (params) {
      const arr = Array.isArray(params) ? params : [params];
      if (!arr.length) return "";
      // 以十字线命中的 x 轴标签反查 index，保证 tooltip 与 axisPointer 指向同一报告期；
      // dataIndex 在跨 grid + dataZoom 时可能与实际悬停点错位，不能直接用。
      let index = labels.indexOf(arr[0].axisValue);
      if (index < 0) index = arr[0].dataIndex;
      if (index == null || index < 0 || !labels[index]) return "";
      const label = labels[index];
      const bar = kline[index];
      // 表头固定为"报告期 + 对应财报周期"，与柱状图、K 线共用同一 index。
      const period = bar && bar.period_start && bar.period_end
        ? esc(bar.period_start) + " ~ " + esc(bar.period_end) +
          (bar.trading_days ? "（" + bar.trading_days + " 个交易日）" : "")
        : "对应报告期";
      const lines = [
        '<div style="font-weight:600;margin-bottom:4px">' + esc(label) +
          "　<span style=\"font-weight:400;color:#64748b\">财报周期 " + period + "</span></div>",
      ];
      if (bar) {
        lines.push('<div style="color:#64748b">季度K线 开 ' + bar.open + " 收 " + bar.close +
          " 低 " + bar.low + " 高 " + bar.high + "</div>");
      } else {
        lines.push('<div style="color:#94a3b8">该财报周期无对应行情（未上市或停牌）</div>');
      }
      const value = scaled(bars[index], barScale);
      if (value != null) {
        lines.push('<div style="border-left:3px solid #3b82f6;padding-left:6px;margin-top:4px">' +
          esc(label) + " " + esc(barName) + " " + value.toFixed(2) + " " + esc(barUnit) + "</div>");
      }
      if (hintFn) {
        const hint = hintFn(label);
        if (hint) {
          lines.push('<div style="margin-top:6px;padding:6px 8px;background:#eff6ff;' +
            'border-left:3px solid #93c5fd;color:#334155;white-space:normal">' +
            '<b>已缓存解释</b>：' + esc(hint) + "</div>");
        }
      }
      return lines.join("");
    };
  }

  // 上方 K 线与下方柱形共用报告期 x 轴，缩放联动。
  function chartOption(labels, kline, barValues, barName, barUnit, barScale, hintFn) {
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
        formatter: tooltip(labels, kline, barValues, barName, barUnit, barScale, hintFn),
        extraCssText: "max-width:420px;white-space:normal;",
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

  // K 线与柱子都代表同一报告期，点击任一处都可打开该期解释。
  // 用 params.name（category 标签）反查下标，避免 dataZoom 下 dataIndex 错位。
  // 每次 setOption 后重绑，避免切换频率/指标时事件仍指向旧序列。
  function bindPeriodClick(chart, labels, handler) {
    chart.off("click");
    chart.on("click", function (params) {
      if (!params || params.componentType !== "series") return;
      if (params.seriesType !== "bar" && params.seriesType !== "candlestick") return;
      const index = labels.indexOf(params.name);
      if (index < 0) return;
      handler(index, params.seriesIndex, params.seriesType);
    });
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
      data.labels, data.kline, metric.values, metric.name, metric.unit, metric.scale,
      function (label) { return cachedHint("primary", "", [metric.key], label); }
    ), true);
    bindPeriodClick(dashChart, data.labels, function (index) {
      openExplanation({
        period: data.labels[index],
        scope: "primary",
        stmtType: "",
        keys: [metric.key],
      });
    });
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
      data.labels, data.kline, metric.values, metric.name, metric.unit, metric.scale,
      function (label) { return cachedHint("statement", state.stmtType, [metric.key], label); }
    ), true);
    bindPeriodClick(stmtChart, data.labels, function (index) {
      openExplanation({
        period: data.labels[index],
        scope: "statement",
        stmtType: state.stmtType,
        keys: [metric.key],
      });
    });

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
      const hint = cachedHint("statement", state.stmtType, [metric.key], data.labels[i]);
      if (hint) tr.title = "已缓存解释：" + hint;
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

  // ===== 资产负债表拆解 =====
  function yi(value) {
    return value == null ? null : value / 1e8;
  }
  function fmtYi(value) {
    const v = yi(value);
    return v == null ? "-" : v.toFixed(2);
  }
  function fmtPct(value) {
    return value == null ? "-" : value.toFixed(2) + "%";
  }

  let investPieChart = null;
  let totalsChart = null;
  let fcfChart = null;

  function renderInvestPie(data) {
    investPieChart = ensureChart("investPie", investPieChart);
    const parts = (data.investment_classes || []).filter((c) => c.latest != null);
    const pieData = parts.map((c) => ({ name: c.name, value: Math.round(yi(c.latest)) }));
    investPieChart.setOption({
      animation: false,
      tooltip: {
        trigger: "item",
        formatter: function (p) {
          return esc(p.name) + "<br>" + p.value + " 亿元（" + p.percent + "%）";
        },
      },
      legend: { type: "scroll", bottom: 0, textStyle: { color: "#94a3b8", fontSize: 10 } },
      series: [{
        type: "pie", radius: ["38%", "68%"], center: ["50%", "44%"],
        label: { color: "#64748b", fontSize: 10, formatter: "{b}\n{d}%" },
        data: pieData.length ? pieData : [{ name: "暂无数据", value: 1 }],
      }],
    }, true);
  }

  function renderBalanceTotals(data) {
    totalsChart = ensureChart("balanceTotals", totalsChart);
    const totals = data.totals || [];
    const names = ["总资产", "流动资产合计", "非流动资产合计", "总负债", "股东权益"];
    const rows = names.map((n) => totals.find((t) => t.name === n)).filter(Boolean);
    totalsChart.setOption({
      animation: false,
      grid: { left: 96, right: 40, top: 16, bottom: 20 },
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" },
        formatter: function (params) {
          const p = params[0];
          return esc(p.name) + "<br>" + p.value + " 亿元";
        },
      },
      xAxis: { type: "value", axisLabel: { color: "#94a3b8", fontSize: 10 } },
      yAxis: {
        type: "category", data: rows.map((r) => r.name),
        axisLabel: { color: "#64748b", fontSize: 11 },
      },
      series: [{
        type: "bar", data: rows.map((r) => {
          const v = yi(r.latest);
          return v == null ? "-" : Math.round(v);
        }),
        itemStyle: { color: "#3b82f6", borderRadius: [0, 3, 3, 0] },
        label: { show: true, position: "right", color: "#64748b", fontSize: 10,
          formatter: function (p) { return p.value === "-" ? "-" : Number(p.value).toLocaleString(); } },
      }],
    }, true);
  }

  function renderInvestTable(data) {
    const table = el("table");
    const head = document.createElement("thead");
    const hr = document.createElement("tr");
    ["类别", "最新金额（亿）", "占总资产", "同比", "包含科目"].forEach((t) => hr.appendChild(el("th", null, t)));
    head.appendChild(hr); table.appendChild(head);
    const tbody = document.createElement("tbody");
    for (const c of data.investment_classes || []) {
      const tr = document.createElement("tr");
      const cells = [
        c.name, fmtYi(c.latest), fmtPct(c.share_of_assets), fmtPct(c.yoy),
        (c.members || []).join("、"),
      ];
      cells.forEach((t) => tr.appendChild(el("td", null, t)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    const box = document.getElementById("investTable");
    box.innerHTML = ""; box.appendChild(table);
    const totalEl = document.getElementById("investTotal");
    const withValue = (data.investment_classes || []).filter((c) => c.latest != null);
    const sum = withValue.reduce((acc, c) => acc + (c.latest || 0), 0);
    totalEl.textContent = withValue.length ? "合计约 " + fmtYi(sum) + " 亿（" + (data.currency || "") + "）" : "";
  }

  function renderBalanceSections(data) {
    const box = document.getElementById("balanceSections");
    box.innerHTML = "";
    for (const [sectionKey, section] of Object.entries(data.sections || {})) {
      const card = el("div", "fx-ratio-card");
      card.appendChild(el("h3", null, section.name));
      (section.items || []).forEach((item, itemIndex) => {
        if (item.latest == null) return; // 财报未单列的项不占空间
        const row = el("div", "fx-ratio-row");
        row.title = "点击迷你走势可放大，叠加股价 K 线与其他科目";
        row.appendChild(el("span", "fx-ratio-name", item.name));
        const holder = el("span", null);
        holder.innerHTML = sparkline(
          (item.values || []).filter((v) => v != null).slice(-12).map((v) => yi(v))
        );
        const svg = holder.querySelector("svg.fx-ratio-spark");
        if (svg) {
          // 迷你图即弹窗入口：索引指向原始 items 数组（跳过未单列项不影响下标）
          svg.classList.add("fx-spark-clickable");
          svg.dataset.sec = sectionKey;
          svg.dataset.item = String(itemIndex);
          svg.setAttribute("title", "点击放大：股价 K 线 + 科目柱状图 + 多科目叠加");
        }
        row.appendChild(holder.firstChild || holder);
        const val = el("span", "fx-ratio-value", fmtYi(item.latest));
        val.appendChild(el("small", null, "亿 · " + fmtPct(item.share_of_assets)));
        row.appendChild(val);
        card.appendChild(row);
      });
      box.appendChild(card);
    }
  }

  function renderFcf(data) {
    const f = data.fcf_history || {};
    const yearly = state.fcfFrequency === "year";
    const src = yearly ? (f.yearly || {}) : f;
    fcfChart = ensureChart("fcfChart", fcfChart);
    const labels = (src.labels || []).filter((_, i) =>
      (src.values || [])[i] != null || (src.ocf || [])[i] != null
    );
    const fcfVals = [], ocfVals = [], priceVals = [], quarterCounts = [];
    (src.labels || []).forEach((label, i) => {
      if (labels.indexOf(label) < 0) return;
      fcfVals.push((src.values || [])[i]);
      ocfVals.push((src.ocf || [])[i]);
      priceVals.push((src.price || [])[i]);
      quarterCounts.push(yearly ? (src.fcf_quarters || [])[i] : null);
    });
    const ccy = data.currency === "HKD" ? "港元" : "元";
    fcfChart.setOption({
      animation: false,
      grid: { left: 56, right: 56, top: 28, bottom: 40 },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" },
        formatter: function (params) {
          const arr = Array.isArray(params) ? params : [params];
          if (!arr.length) return "";
          const i = arr[0].dataIndex;
          const lines = ['<div style="font-weight:600;margin-bottom:4px">' + esc(labels[i]) + "</div>"];
          arr.forEach(function (p) {
            if (p.value == null || p.value === "-") return;
            const isPrice = p.seriesName === "期末股价";
            const color = p.seriesName === "自由现金流" ? "#3b82f6" :
              (p.seriesName === "经营现金流" ? "#f59e0b" : "#10b981");
            lines.push('<div><span style="color:' + color + '">' + (isPrice ? "●" : "■") + "</span> " +
              esc(p.seriesName) + " " + p.value + (isPrice ? " " + ccy : " 亿元") + "</div>");
          });
          if (yearly && quarterCounts[i]) {
            lines.push('<div style="color:#64748b">自由现金流为全年 ' + quarterCounts[i] +
              " 个已披露季度合计</div>");
          }
          return lines.join("");
        } },
      legend: { data: ["自由现金流", "经营现金流", "期末股价"], top: 0,
        textStyle: { color: "#94a3b8", fontSize: 10 } },
      xAxis: { type: "category", data: labels, axisLabel: { color: "#94a3b8", fontSize: 10, hideOverlap: true } },
      yAxis: [
        { type: "value", name: "亿元", nameTextStyle: { color: "#94a3b8", fontSize: 10 },
          axisLabel: { color: "#94a3b8", fontSize: 10 }, splitLine: { show: false } },
        { type: "value", name: ccy, position: "right",
          nameTextStyle: { color: "#94a3b8", fontSize: 10 },
          axisLabel: { color: "#10b981", fontSize: 10 }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside", start: zoomStart(labels.length), end: 100 },
        { type: "slider", height: 16, bottom: 6, start: zoomStart(labels.length), end: 100,
          textStyle: { color: "#94a3b8", fontSize: 10 } }],
      series: [
        { name: "自由现金流", type: "bar", barMaxWidth: 20, barGap: "20%",
          data: fcfVals.map((v) => v == null ? "-" : v),
          itemStyle: { color: "#3b82f6", borderRadius: [2, 2, 0, 0] } },
        { name: "经营现金流", type: "bar", barMaxWidth: 20,
          data: ocfVals.map((v) => v == null ? "-" : v),
          itemStyle: { color: "#f59e0b", borderRadius: [2, 2, 0, 0] } },
        { name: "期末股价", type: "line", yAxisIndex: 1,
          data: priceVals.map((v) => v == null ? "-" : v),
          smooth: true, showSymbol: false, connectNulls: true,
          lineStyle: { color: "#10b981", width: 2 }, itemStyle: { color: "#10b981" } },
      ],
    }, true);
    document.getElementById("fcfHint").textContent = yearly
      ? "按自然年：FCF 全年合计 · OCF 年末累计 · 绿线为年末收盘价（右轴 " + ccy + "）"
      : "按财报单季：FCF 原文单季值 · OCF 差分单季值 · 绿线为期末收盘价（右轴 " + ccy + "）";
    document.getElementById("fcfNote").textContent = (src.source || f.source || "") +
      (f.available ? "" : " · 当前标的暂无可抽取的历史 FCF");
  }

  // ===== 科目分组明细弹出层：点击迷你走势放大为 K 线 + 多科目柱状图 =====
  let detailChart = null;

  function detailItem(ref) {
    const sec = state.balanceData && state.balanceData.sections
      ? state.balanceData.sections[ref.sec] : null;
    return sec && sec.items ? sec.items[ref.index] : null;
  }

  function sameRef(a, b) {
    return Boolean(a && b && a.sec === b.sec && a.index === b.index);
  }

  function roundYi(value) {
    const v = yi(value);
    return v == null ? null : Math.round(v * 100) / 100;
  }

  function openDetail(ref) {
    const item = detailItem(ref);
    if (!item) return;
    state.detailPrimary = { sec: ref.sec, index: ref.index, name: item.name };
    state.detailOverlay = [];
    const modal = document.getElementById("detailModal");
    modal.hidden = false;
    syncModalScroll();
    document.getElementById("detailTitle").textContent = item.name + " · 历史趋势与股价";
    document.getElementById("detailMeta").textContent =
      "最新 " + fmtYi(item.latest) + " 亿 · 占总资产 " + fmtPct(item.share_of_assets) +
      " · 币种 " + (state.balanceData.currency || "-");
    document.getElementById("detailOverlayPanel").hidden = true;
    buildOverlayList();
    renderDetail();
  }

  function closeDetail() {
    document.getElementById("detailModal").hidden = true;
    syncModalScroll();
  }

  function buildOverlayList() {
    const box = document.getElementById("detailOverlayList");
    box.innerHTML = "";
    const sections = (state.balanceData && state.balanceData.sections) || {};
    for (const [secKey, sec] of Object.entries(sections)) {
      (sec.items || []).forEach((item, index) => {
        if (item.latest == null) return;
        const ref = { sec: secKey, index };
        if (sameRef(ref, state.detailPrimary)) return;
        const label = el("label", "fx-overlay-item");
        label.title = sec.name + " · " + item.name;
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = state.detailOverlay.some((o) => sameRef(o, ref));
        cb.addEventListener("change", function () {
          if (cb.checked) {
            if (state.detailOverlay.length >= 3) {
              cb.checked = false; // 主项 + 最多 3 个叠加 = 4 种数据
              return;
            }
            state.detailOverlay.push(ref);
          } else {
            state.detailOverlay = state.detailOverlay.filter((o) => !sameRef(o, ref));
          }
          updateOverlayCount();
          renderDetail();
        });
        label.appendChild(cb);
        label.appendChild(el("span", null, item.name));
        box.appendChild(label);
      });
    }
    updateOverlayCount();
  }

  function updateOverlayCount() {
    document.getElementById("detailOverlayCount").textContent = String(state.detailOverlay.length);
  }

  function renderDetail() {
    const data = state.balanceData;
    if (!data || !state.detailPrimary) return;
    const isYear = state.detailFreq === "year";
    const src = data.period_kline && data.period_kline[isYear ? "year" : "quarter"];
    if (!src) {
      showError("detailChart", "大图数据未就绪，请刷新资产负债表拆解后重试");
      return;
    }
    const refs = [state.detailPrimary].concat(state.detailOverlay);
    const lines = refs.map((ref, i) => {
      const item = detailItem(ref) || {};
      return {
        name: item.name || "-",
        values: isYear ? (item.yearly_values || []) : (item.values || []),
        color: DETAIL_COLORS[i % DETAIL_COLORS.length],
      };
    });
    const kline = src.kline || [];
    const indices = [];
    for (let i = 0; i < (src.labels || []).length; i++) {
      // 无行情且所有待展示科目均无值的期不画，避免早年空档占满 x 轴
      if (kline[i] || lines.some((s) => s.values[i] != null)) indices.push(i);
    }
    const labels = indices.map((i) => src.labels[i]);
    const candles = indices.map((i) => kline[i] || null);
    const barData = lines.map((s) => indices.map((i) => {
      const v = roundYi(s.values[i]);
      return v == null ? "-" : v;
    }));
    // 点击回调需要「展示标签 → 原始序列下标 → 指标键」的映射（indices 已过滤空期）
    const detailContext = {
      labels: labels,
      originalIndices: indices,
      keys: lines.map((s) => s.name),
      scope: "balance",
      stmtType: "",
    };
    detailChart = ensureChart("detailChart", detailChart);
    detailChart.setOption({
      animation: false,
      grid: [
        { left: 70, right: 24, top: 30, height: "36%" },
        { left: 70, right: 24, top: "54%", height: "32%" },
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
        { scale: true, gridIndex: 1, name: "亿元",
          nameTextStyle: { color: "#64748b", fontSize: 10 },
          axisLabel: { color: "#64748b", fontSize: 10 }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: zoomStart(labels.length), end: 100 },
        { type: "slider", xAxisIndex: [0, 1], height: 18, bottom: 6,
          start: zoomStart(labels.length), end: 100,
          textStyle: { color: "#94a3b8", fontSize: 10 } },
      ],
      legend: {
        data: lines.map((s) => s.name), top: 2,
        textStyle: { color: "#94a3b8", fontSize: 10 },
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }] },
        extraCssText: "max-width:420px;white-space:normal;",
        formatter: function (params) {
          const arr = Array.isArray(params) ? params : [params];
          if (!arr.length) return "";
          let index = labels.indexOf(arr[0].axisValue);
          if (index < 0) index = arr[0].dataIndex;
          if (index < 0 || !labels[index]) return "";
          const bar = candles[index];
          const out = ['<div style="font-weight:600;margin-bottom:4px">' + esc(labels[index]) + "</div>"];
          if (bar) {
            out.push('<div style="color:#64748b">K线 开 ' + bar.open + " 收 " + bar.close +
              " 低 " + bar.low + " 高 " + bar.high +
              (bar.trading_days ? "（" + bar.trading_days + " 个交易日）" : "") + "</div>");
          } else {
            out.push('<div style="color:#94a3b8">该报告期无对应行情</div>');
          }
          lines.forEach(function (s) {
            const v = roundYi(s.values[indices[index]]);
            if (v == null) return;
            out.push('<div><span style="color:' + s.color + '">■</span> ' + esc(s.name) +
              " " + v.toFixed(2) + " 亿元</div>");
          });
          const barParam = arr.find(function (p) { return p.seriesType === "bar"; });
          const itemIndex = barParam ? barParam.seriesIndex - 1 : 0;
          const keys = itemIndex > 0
            ? [detailContext.keys[itemIndex]]
            : detailContext.keys.slice(0);
          const hint = cachedHint("balance", "", keys, labels[index]);
          if (hint) {
            out.push('<div style="margin-top:6px;padding:6px 8px;background:#eff6ff;' +
              'border-left:3px solid #93c5fd;color:#334155;white-space:normal">' +
              "<b>已缓存解释</b>：" + esc(hint) + "</div>");
          }
          return out.join("");
        },
      },
      series: [
        {
          name: "报告期K线", type: "candlestick", xAxisIndex: 0, yAxisIndex: 0,
          data: candles.map((b) => b ? [b.open, b.close, b.low, b.high] : "-"),
          itemStyle: { color: "#dc2626", color0: "#16a34a",
            borderColor: "#dc2626", borderColor0: "#16a34a" },
        },
      ].concat(lines.map((s, i) => ({
        name: s.name, type: "bar", xAxisIndex: 1, yAxisIndex: 1,
        barMaxWidth: 22, barGap: "20%",
        data: barData[i],
        itemStyle: { color: s.color, borderRadius: [2, 2, 0, 0] },
      }))),
    }, true);
    // seriesIndex 0 是 K 线，1..N 依次是主项与叠加项；点击柱子对该指标组做解释
    bindPeriodClick(detailChart, labels, function (dataIndex, seriesIndex, seriesType) {
      const original = detailContext.originalIndices[dataIndex];
      const src = data.period_kline[isYear ? "year" : "quarter"];
      const itemIndex = seriesType === "candlestick" ? 0 : seriesIndex - 1;
      // 点主柱或 K 线：把叠加的多指标一起解释；点叠加柱：只解释该项
      const keys = itemIndex > 0
        ? [detailContext.keys[itemIndex]]
        : detailContext.keys.slice(0);
      openExplanation({
        period: src.labels[original],
        scope: "balance",
        stmtType: "",
        keys: keys,
      });
    });
    requestAnimationFrame(function () { detailChart.resize(); });
    document.getElementById("detailHint").textContent =
      "共 " + labels.length + " 期 · " + (isYear ? "年度（取当年末时点值）" : "单季时点值") +
      " · 叠加 " + state.detailOverlay.length + "/3";
  }

  // ===== 单期指标解释弹窗 =====
  let explainRequest = null;
  let explainSeq = 0; // 快速切换报告期时丢弃过期的缓存读取结果

  function syncModalScroll() {
    const detailOpen = !document.getElementById("detailModal").hidden;
    const explainOpen = !document.getElementById("explainModal").hidden;
    document.body.style.overflow = (detailOpen || explainOpen) ? "hidden" : "";
  }

  function explainParams() {
    const params = new URLSearchParams({
      period: explainRequest.period,
      scope: explainRequest.scope,
      keys: explainRequest.keys.join(","),
    });
    if (explainRequest.stmtType) params.set("stmt_type", explainRequest.stmtType);
    return params;
  }

  // 打开弹窗时先读缓存：命中则秒显历史解释，绝不触发模型调用。
  async function loadCachedExplanation(seq) {
    const params = explainParams();
    params.set("cached_only", "true");
    try {
      const result = await getJSON(
        "/api/stock/" + symbol + "/metric-explanation?" + params.toString()
      );
      if (seq !== explainSeq || !result.available) return;
      renderExplanation(result, "cache");
    } catch (error) {
      // 缓存读取失败不影响用户手动生成
    }
  }

  function openExplanation(request) {
    const seq = ++explainSeq;
    explainRequest = request;
    document.getElementById("explainModal").hidden = false;
    syncModalScroll();
    document.getElementById("explainTitle").textContent =
      request.period + " · " + request.keys.join("、");
    document.getElementById("explainMeta").textContent =
      "点击生成后读取该报告期财报原文 · 数字由代码计算，模型只做解释";
    document.getElementById("explainFacts").innerHTML = "";
    document.getElementById("explainText").textContent =
      "点击「生成解释」后，系统会先让模型确定检索词、读取对应报告期财报原文，再生成解释。";
    document.getElementById("explainStatus").textContent = "检查历史缓存…";
    document.getElementById("explainEvidenceBox").hidden = true;
    loadCachedExplanation(seq);
  }

  function closeExplanation() {
    document.getElementById("explainModal").hidden = true;
    syncModalScroll();
  }

  function renderExplanation(result, mode) {
    const factsBox = document.getElementById("explainFacts");
    factsBox.innerHTML = "";
    for (const item of result["指标"] || []) {
      const card = el("div", "fx-explain-fact");
      card.appendChild(el("b", null, item["指标"]));
      const value = el("span", null,
        (item["当期值"] == null ? "-" : Number(item["当期值"]).toFixed(2)) +
        " " + (item["单位"] || ""));
      card.appendChild(value);
      const yoy = item["同比%"] == null ? "-" : Number(item["同比%"]).toFixed(2) + "%";
      const qoq = item["环比%"] == null ? "-" : Number(item["环比%"]).toFixed(2) + "%";
      card.appendChild(el("small", null, "同比 " + yoy + " · 环比 " + qoq));
      factsBox.appendChild(card);
    }
    document.getElementById("explainText").textContent = result.text || "";
    const evidence = result["财报原文证据"] || [];
    const evBox = document.getElementById("explainEvidenceBox");
    const evList = document.getElementById("explainEvidence");
    evList.innerHTML = "";
    if (evidence.length) {
      for (const e of evidence) {
        const div = el("div", "fx-evidence-item fx-evidence-item-evidence");
        div.appendChild(el("div", "fx-evidence-meta",
          (e.source || "-") + " · " + (e.period || "-") + " · 命中：" + (e.keywords || "-")));
        div.appendChild(el("div", "fx-evidence-text", e.text));
        evList.appendChild(div);
      }
      evBox.hidden = false;
    } else {
      evBox.hidden = true;
    }
    state.explainIndex[indexKey(
      explainRequest.scope, explainRequest.stmtType || "", explainRequest.keys, explainRequest.period
    )] = {
      period: explainRequest.period,
      scope: explainRequest.scope,
      stmt_type: explainRequest.stmtType || "",
      keys: explainRequest.keys.slice(0),
      summary: String(result.text || "").replace(/\s+/g, " ").trim().slice(0, 240),
      generated_at: result.generated_at,
    };
    const cacheLabel = result.cached ? "命中缓存" : (mode === "refresh" ? "已重新生成" : "本次生成");
    document.getElementById("explainStatus").textContent =
      cacheLabel + " · " + (result.source === "llm" ? "模型生成" : "模板回退") +
      (result["检索词来源"] ? " · 检索词" + (result["检索词来源"] === "llm" ? "由模型确定" : "按词表降级") : "") +
      (result.generated_at ? " · " + result.generated_at.slice(0, 16).replace("T", " ") : "");
  }

  async function generateExplanation(refresh) {
    if (!explainRequest) return;
    const seq = explainSeq;
    const status = document.getElementById("explainStatus");
    const text = document.getElementById("explainText");
    status.textContent = "生成中：确定检索词 → 读取财报原文 → 解释…";
    text.textContent = "生成中…";
    try {
      const params = explainParams();
      if (refresh) params.set("refresh", "true");
      const result = await getJSON(
        "/api/stock/" + symbol + "/metric-explanation?" + params.toString()
      );
      if (seq !== explainSeq) return;
      if (!result.available) {
        status.textContent = "提示";
        text.textContent = result.text || "暂无可用数据";
        return;
      }
      renderExplanation(result, refresh ? "refresh" : "generate");
    } catch (error) {
      if (seq !== explainSeq) return;
      status.textContent = "生成失败";
      text.textContent = error && error.message ? error.message : String(error);
    }
  }

  function renderAssociateEvidence(data) {
    const box = document.getElementById("associateEvidence");
    box.innerHTML = "";
    const items = data.associate_evidence || [];
    if (!items.length) {
      box.appendChild(el("div", "fx-note", "未检索到联营/合营公司相关原文片段。"));
      return;
    }
    for (const e of items) {
      const div = el("div", "fx-evidence-item");
      div.appendChild(el("div", "fx-evidence-meta", (e.category || "") + " · " + (e.source || "")));
      div.appendChild(el("div", "fx-evidence-text", e.text));
      box.appendChild(div);
    }
  }

  async function loadBalanceBreakdown() {
    document.getElementById("balanceMeta").textContent = "加载中…";
    try {
      const data = await getJSON("/api/stock/" + symbol + "/balance-breakdown");
      state.balanceData = data;
      document.getElementById("balanceMeta").textContent =
        "报告期 " + (data.labels.length ? data.labels[data.labels.length - 1] : "-") +
        " · 币种 " + (data.currency || "-") + " · 共 " + data.labels.length + " 期";
      renderInvestPie(data);
      renderBalanceTotals(data);
      renderInvestTable(data);
      renderBalanceSections(data);
      renderFcf(data);
      renderAssociateEvidence(data);
    } catch (error) {
      showError("balanceMeta", error);
    }
  }

  async function loadBalanceNarrative(refresh) {
    const box = document.getElementById("balanceNarrative");
    box.textContent = "解读生成中…";
    try {
      const result = await getJSON(
        "/api/stock/" + symbol + "/balance-narrative" + (refresh ? "?refresh=true" : "")
      );
      box.innerHTML = String(result.text || "").split(/\n\s*\n/)
        .map((p) => "<p>" + esc(p) + "</p>").join("");
    } catch (error) {
      showError("balanceNarrative", error);
    }
  }

  function setFrequency(freq) {
    state.frequency = freq;
    state.stmtKey = null;
    // 顶部与三大表两处频率控件保持同步
    document.querySelectorAll("#freqSwitch button, #stmtFreqSwitch button").forEach((b) => {
      b.classList.toggle("active", b.dataset.freq === freq);
    });
    loadDashboard().catch((error) => showError("dashboardChart", error));
  }

  function bindControls() {
    document.querySelectorAll("#freqSwitch button, #stmtFreqSwitch button").forEach((button) => {
      button.addEventListener("click", () => setFrequency(button.dataset.freq));
    });
    document.querySelectorAll("#fcfFreqSwitch button").forEach((button) => {
      button.addEventListener("click", function () {
        document.querySelectorAll("#fcfFreqSwitch button").forEach((b) => b.classList.remove("active"));
        button.classList.add("active");
        state.fcfFrequency = button.dataset.freq;
        if (state.balanceData) renderFcf(state.balanceData);
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
    const refreshBalance = document.getElementById("refreshBalanceNarrative");
    if (refreshBalance) refreshBalance.addEventListener("click", function () {
      loadBalanceNarrative(true);
    });
    // 科目分组明细：点击迷你走势打开大图
    document.getElementById("balanceSections").addEventListener("click", function (event) {
      const target = event.target;
      const svg = target && target.closest ? target.closest(".fx-spark-clickable") : null;
      if (!svg) return;
      openDetail({ sec: svg.dataset.sec, index: Number(svg.dataset.item) });
    });
    document.querySelectorAll("[data-close-detail]").forEach(function (node) {
      node.addEventListener("click", closeDetail);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if (!document.getElementById("explainModal").hidden) closeExplanation();
      else if (!document.getElementById("detailModal").hidden) closeDetail();
    });
    document.querySelectorAll("#detailFreqSwitch button").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll("#detailFreqSwitch button").forEach((b) => b.classList.remove("active"));
        button.classList.add("active");
        state.detailFreq = button.dataset.freq;
        renderDetail();
      });
    });
    document.getElementById("detailOverlayBtn").addEventListener("click", function () {
      const panel = document.getElementById("detailOverlayPanel");
      panel.hidden = !panel.hidden;
      if (!panel.hidden) buildOverlayList();
    });
    document.getElementById("detailOverlayClear").addEventListener("click", function () {
      state.detailOverlay = [];
      buildOverlayList();
      renderDetail();
    });
    document.querySelectorAll("[data-close-explain]").forEach(function (node) {
      node.addEventListener("click", closeExplanation);
    });
    document.getElementById("explainGenerate").addEventListener("click", function () {
      generateExplanation(false);
    });
    document.getElementById("explainRefresh").addEventListener("click", function () {
      generateExplanation(true);
    });
  }

  bindControls();
  loadExplanationIndex();
  loadDashboard().catch((error) => showError("dashboardChart", error));
  loadNarrative(false);
  loadBalanceBreakdown().catch((error) => showError("balanceMeta", error));
  loadBalanceNarrative(false);
  renderComposition().catch((error) => showError("composition", error));
  renderHolders().catch((error) => showError("holders", error));
})();
