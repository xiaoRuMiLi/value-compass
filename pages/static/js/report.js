(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";

  const { getJSON } = window.VC;

  // K 线与估值叠加折线共用一条时间轴：两路数据都就绪后合成
  let klineChart = null;
  let klineBars = [];
  let valuationHistory = null;

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
    growth: "预测增长率",
    discount: "折现率（要求回报率）",
    years: "预测年数",
    terminal_growth: "10年后永续增长率",
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
    if (typeof value === "object") return "见口径说明";
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
      if (key === "formula" || key === "param_source") continue;
      parts.push(`${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`);
    }
    for (const [key, value] of Object.entries(item.inputs || {})) {
      parts.push(`${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`);
    }
    return parts.join("；");
  }

  function renderScore(score) {
    const box = document.getElementById("dimensions");
    box.innerHTML = "";

    const dims = score.dimensions || [];
    const minScore = dims.length ? Math.min(...dims.map((d) => d.score)) : null;

    const summary = el("div", "score-summary");
    const total = el("div", "score-total");
    total.appendChild(el("span", "score-total-num", Number(score.total).toFixed(1)));
    total.appendChild(el("span", "score-total-label", "综合得分 / 100"));
    summary.appendChild(total);

    const ratingWrap = el("div", "score-rating-wrap");
    ratingWrap.appendChild(el("span", "score-rating-label", "评级"));
    ratingWrap.appendChild(el("span", "score-rating-badge", score.rating || "-"));
    ratingWrap.appendChild(el("span", "score-rating-hint", scoreHint(score.total)));
    summary.appendChild(ratingWrap);

    const scale = el("div", "score-scale");
    for (const tick of [
      { at: 0, label: "0 回避" },
      { at: 60, label: "60 观望" },
      { at: 70, label: "70 推荐" },
      { at: 80, label: "80 强烈推荐" },
    ]) {
      const mark = el("div", "score-scale-tick");
      mark.style.left = `${tick.at}%`;
      mark.appendChild(el("span", "score-scale-mark-label", tick.label));
      scale.appendChild(mark);
    }
    const pointer = el("div", "score-scale-pointer");
    pointer.style.left = `${Math.max(0, Math.min(100, score.total))}%`;
    scale.appendChild(pointer);
    summary.appendChild(scale);

    if (score.red_flags && score.red_flags.length) {
      const flagBox = el("div", "score-flags");
      flagBox.appendChild(el("div", "score-flags-title", "风险提示"));
      for (const flag of score.red_flags) {
        const tag = el(
          "span",
          "score-flag",
          `[${flag.severity || flag.flag_type || "提示"}] ${flag.description || ""}`
        );
        flagBox.appendChild(tag);
      }
      summary.appendChild(flagBox);
    }
    box.appendChild(summary);

    for (const dimension of dims) {
      const block = el("div", "dim-block");
      const head = el("div", "dim-head");
      const labelWrap = el("div", "dim-label-wrap");
      labelWrap.appendChild(el("span", "dim-label", dimension.dimension));
      if (dimension.score === minScore && dims.length > 1) {
        labelWrap.appendChild(el("span", "dim-weak", "最弱"));
      }
      head.appendChild(labelWrap);
      head.appendChild(
        el(
          "span",
          "dim-value",
          `${dimension.score.toFixed(1)} 分 · 权重 ${dimension.weight}% · 贡献 +${(
            (dimension.score * dimension.weight) / 100
          ).toFixed(1)}`
        )
      );
      const bar = el("div", "dim-bar");
      const fill = el("div", `dim-fill ${scoreFillClass(dimension.score)}`);
      fill.style.width = `${Math.max(0, Math.min(100, dimension.score))}%`;
      bar.appendChild(fill);
      block.appendChild(head);
      block.appendChild(bar);

      if (dimension.detail) {
        const details = document.createElement("details");
        details.className = "dim-details";
        const summaryEl = el("summary", "dim-details-summary", "查看计算明细");
        details.appendChild(summaryEl);
        details.appendChild(el("div", "dim-detail", dimension.detail));
        block.appendChild(details);
      }
      box.appendChild(block);
    }
  }

  function scoreHint(total) {
    if (total >= 80) return "评分优秀";
    if (total >= 70) return "评分良好";
    if (total >= 60) return "中性区间";
    return "评分偏低";
  }

  function scoreFillClass(score) {
    if (score >= 70) return "dim-fill-good";
    if (score >= 45) return "dim-fill-mid";
    return "dim-fill-low";
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
    const names = { relative: "相对估值", graham: "格雷厄姆" };
    // DDM/DCF 只在下方「估值参数校准」展示一套，避免上下重复同一组区间。
    for (const item of (valuation || []).filter(
      (v) => v.method === "relative" || v.method === "graham"
    )) {
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
    box.innerHTML = "";
    const labels = { buffett: "巴菲特", duan_yongping: "段永平" };
    for (const master of masters) {
      const card = el("div", "master-card");
      card.id = `master-${master.master}`;
      card.appendChild(el("div", "master-head", `${labels[master.master] || master.master}：${master.verdict}（${master.score}）`));
      for (const signal of master.signals) {
        card.appendChild(el("div", "signal", `${signal.passed ? "✓" : "✗"} ${signal.name} — ${signal.detail || ""}`));
      }
      if (master.narrative) {
        card.appendChild(el("div", "master-note", master.narrative));
      } else {
        card.appendChild(el("div", "master-note master-loading", "正在生成大师视角解读…"));
      }
      box.appendChild(card);
    }
  }

  function renderReview(review, moat, cycle, fraud, delisting) {
    const tags = document.getElementById("tags");
    tags.appendChild(el("span", "tag", `护城河：${moat.rating}`));
    tags.appendChild(el("span", "tag", `周期：${cycle.position}`));
    if (fraud.score != null) tags.appendChild(el("span", "tag", `造假风险：${fraud.score}`));
    if (delisting.score != null) tags.appendChild(el("span", "tag", `退市风险：${delisting.score}`));
  }

  // K 线叠加三条估值折线：PE(TTM)、PB(MRQ) 按交易日对齐；ROE 按年报报告期锚定到最近交易日。
  // 三条线用各自独立的 Y 轴，避免量纲差异把它们压扁；蜡烛用左轴。
  const OVERLAY_COLORS = { pe: "#7c3aed", pb: "#0891b2", roe: "#d97706" };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function overlayTooltip(params) {
    const arr = Array.isArray(params) ? params : [params];
    const lines = [];
    const candle = arr.find((p) => p.seriesType === "candlestick");
    if (candle) {
      lines.push('<div class="kl-tt-date">' + esc(candle.axisValue || candle.name || "") + "</div>");
      const raw = (candle.data && candle.data.value) ? candle.data.value : (candle.data || candle.value);
      if (Array.isArray(raw)) {
        const nums = raw.filter((x) => typeof x === "number");
        if (nums.length >= 4) {
          const [o, c, l, h] = nums.slice(-4);
          lines.push('<div class="kl-tt-ohlc">开 ' + o + "　收 " + c + "　低 " + l + "　高 " + h + "</div>");
        }
      }
    }
    for (const p of arr) {
      if (!["pe", "pb", "roe"].includes(p.seriesName) || p.value == null) continue;
      const label = { pe: "市盈率 PE", pb: "市净率 PB", roe: "ROE" }[p.seriesName];
      lines.push(
        '<div class="kl-tt-meta" style="border-left:3px solid ' + OVERLAY_COLORS[p.seriesName] + ';padding-left:6px">'
        + label + " " + Number(p.value).toFixed(2) + (p.seriesName === "roe" ? "%" : "") + "</div>"
      );
    }
    return lines.join("") || "";
  }

  // 把估值序列对齐到 K 线交易日 index：PE/PB 用 exact trade_date，ROE 用「报告期末或之后最近交易日」
  function alignOverlay(hist) {
    const dates = klineBars.map((b) => b.trade_date);
    const idxByDate = new Map(dates.map((d, i) => [d, i]));
    const pe = [], pb = [], roe = [];
    for (const d of dates) { pe.push(null); pb.push(null); roe.push(null); }
    for (const v of (hist && hist.valuations) || []) {
      const i = idxByDate.get(v.trade_date);
      if (i == null) continue;
      if (v.pe_ttm != null) pe[i] = Number(v.pe_ttm);
      if (v.pb_mrq != null) pb[i] = Number(v.pb_mrq);
    }
    for (const r of (hist && hist.roe) || []) {
      if (r.roe == null) continue;
      let i = dates.findIndex((d) => d >= r.report_date);
      if (i < 0) i = dates.length - 1;
      roe[i] = Number(r.roe);
    }
    return { pe, pb, roe };
  }

  function applyOverlay() {
    if (!klineChart || !valuationHistory) return;
    const series = alignOverlay(valuationHistory);
    klineChart.setOption({
      series: [
        {},
        { data: series.pe },
        { data: series.pb },
        { data: series.roe },
      ],
    });
  }

  function renderKline(kline) {
    klineBars = kline || [];
    if (!klineChart) {
      klineChart = echarts.init(document.getElementById("kline"));
      window.addEventListener("resize", function () {
        if (klineChart) klineChart.resize();
      });
    }
    klineChart.setOption({
      animation: false,
      grid: { left: 56, right: 52, top: 36, bottom: 60 },
      legend: {
        data: ["市盈率 PE", "市净率 PB", "ROE"],
        top: 4,
        textStyle: { color: "#64748b", fontSize: 11 },
      },
      xAxis: { type: "category", data: klineBars.map((b) => b.trade_date) },
      yAxis: [
        { scale: true, name: "价格", nameTextStyle: { color: "#94a3b8", fontSize: 10 } },
        { scale: true, position: "right", name: "PE", nameTextStyle: { color: OVERLAY_COLORS.pe, fontSize: 10 },
          axisLabel: { color: OVERLAY_COLORS.pe }, splitLine: { show: false } },
        { scale: true, position: "right", offset: 38, name: "PB", nameTextStyle: { color: OVERLAY_COLORS.pb, fontSize: 10 },
          axisLabel: { color: OVERLAY_COLORS.pb }, splitLine: { show: false } },
        { scale: true, position: "right", offset: 76, name: "ROE%", nameTextStyle: { color: OVERLAY_COLORS.roe, fontSize: 10 },
          axisLabel: { color: OVERLAY_COLORS.roe }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20, bottom: 8 }],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        formatter: overlayTooltip,
      },
      series: [
        {
          name: "日K",
          type: "candlestick",
          yAxisIndex: 0,
          data: klineBars.map((b) => [b.open, b.close, b.low, b.high]),
        },
        { name: "市盈率 PE", type: "line", yAxisIndex: 1, showSymbol: false, connectNulls: true,
          lineStyle: { width: 1.5, color: OVERLAY_COLORS.pe }, itemStyle: { color: OVERLAY_COLORS.pe }, data: [] },
        { name: "市净率 PB", type: "line", yAxisIndex: 2, showSymbol: false, connectNulls: true,
          lineStyle: { width: 1.5, color: OVERLAY_COLORS.pb }, itemStyle: { color: OVERLAY_COLORS.pb }, data: [] },
        { name: "ROE", type: "line", yAxisIndex: 3, showSymbol: false, connectNulls: true,
          lineStyle: { width: 1.5, color: OVERLAY_COLORS.roe }, itemStyle: { color: OVERLAY_COLORS.roe }, data: [] },
      ],
    }, true);
    applyOverlay();
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

  const pct1 = (v) => (v == null ? "-" : `${(v * 100).toFixed(1)}%`);

  // 把模型的 g/r 翻译成用户能直接读懂的中文，并明确增长率适用的年限。
  function describeParams(method, p) {
    if (!p) return "";
    if (method === "ddm") {
      return `股息长期增长率 ${pct1(p.growth)}（长期永续） · 折现率 ${pct1(p.discount)}`;
    }
    return `前 10 年现金流增长率 ${pct1(p.growth)} · 10 年后永续增长率 ${pct1(p.terminal_growth)} · 折现率 ${pct1(p.discount)}`;
  }

  // 情景行空间有限，用更紧凑的中文表述，年限含义与基准卡片一致。
  function describeScenarioParams(method, p) {
    if (!p) return "-";
    if (method === "ddm") {
      return `永续增长率 ${pct1(p.growth)} · 折现率 ${pct1(p.discount)}`;
    }
    return `前10年 ${pct1(p.growth)} · 永续 ${pct1(p.terminal_growth)} · 折现率 ${pct1(p.discount)}`;
  }

  const PARAM_NOTES = {
    ddm: "股息增长率按长期永续假设（无固定预测年限）；折现率即要求回报率，越高则合理价越低。合理区间为中枢 ±20%。",
    dcf: "前 10 年为可预测增长期，第 11 年起按永续增长率计算终值；折现率即要求回报率，越高则合理价越低。合理区间为中枢 ±20%。",
  };

  // 悲观/基准/乐观三档对照：输入相同，只变增长与折现假设。
  function renderScenarioRows(data, method) {
    const cal = data.calibrated || {};
    const params = data.params || {};
    const scCal = cal.scenarios || {};
    const scP = params.scenarios || {};
    const rows = [
      { key: "pessimistic", label: "悲观", v: (scCal.pessimistic || {})[method], p: (scP.pessimistic || {})[method] },
      { key: "base", label: "基准", v: cal[method], p: params[method] },
      { key: "optimistic", label: "乐观", v: (scCal.optimistic || {})[method], p: (scP.optimistic || {})[method] },
    ];
    const box = el("div", "vp-scenarios");
    box.appendChild(el("div", "vp-sc-head", "情景估计（同一输入，不同假设）"));
    for (const row of rows) {
      if (!row.v || row.v.fair_value_low == null) continue;
      const line = el("div", `vp-sc-row sc-${row.key}`);
      line.appendChild(el("span", "vp-sc-tag", row.label));
      line.appendChild(el("span", "vp-sc-params", describeScenarioParams(method, row.p)));
      line.appendChild(el("span", "vp-sc-range", `${row.v.fair_value_low} ~ ${row.v.fair_value_high}`));
      box.appendChild(line);
    }
    return box;
  }

  function renderValuationParams(data) {
    const box = document.getElementById("valuationParams");
    box.innerHTML = "";
    const title = el("div", "vp-title", "估值参数校准（结合财报与研报，动态调整假设）");
    box.appendChild(title);

    const source = el(
      "span",
      "vp-source",
      data.source === "llm"
        ? `LLM 校准 · 参考 ${data.research_count || 0} 篇研报`
        : "模板校准（未启用 LLM）"
    );
    box.appendChild(source);

    const params = data.params || {};
    const cal = data.calibrated || {};

    const overall = cal.overall;
    if (overall && overall.low != null) {
      const hero = el("div", "vp-overall");
      hero.appendChild(el("div", "vp-overall-label", "综合合理股价区间"));
      hero.appendChild(el("div", "vp-overall-range", `${overall.low} ~ ${overall.high} 元`));
      const summary = [`中枢 ${overall.center} 元`];
      if (overall.current_price != null) summary.push(`现价 ${overall.current_price} 元`);
      if (overall.margin_of_safety != null) {
        summary.push(`相对现价 ${fmtPct(overall.margin_of_safety)}`);
      }
      hero.appendChild(el("div", "vp-overall-center", summary.join(" · ")));
      const scCal = cal.scenarios || {};
      const scParts = [];
      for (const [key, label] of [["pessimistic", "悲观"], ["optimistic", "乐观"]]) {
        const o = (scCal[key] || {}).overall;
        if (o && o.low != null) scParts.push(`${label} ${o.low} ~ ${o.high}`);
      }
      if (scParts.length) {
        hero.appendChild(el("div", "vp-overall-scenarios", scParts.join("　｜　")));
      }
      if (overall.note) hero.appendChild(el("div", "vp-overall-note", overall.note));
      box.appendChild(hero);
    }

    const sellside = data.sellside;
    if (sellside && sellside.total) {
      const card = el("div", "vp-sellside");
      card.appendChild(el("div", "vp-card-head", "卖方观点与分歧度"));
      if (sellside.summary) card.appendChild(el("div", "vp-ss-summary", sellside.summary));
      const ratingNames = { buy: "买入", overweight: "增持", neutral: "中性", underweight: "减持", sell: "卖出" };
      const dist = sellside.rating_distribution || {};
      const chips = Object.keys(dist).map((key) => `${ratingNames[key] || key} ${dist[key]} 家`);
      if (chips.length) card.appendChild(el("div", "vp-ss-chips", chips.join(" · ")));
      box.appendChild(card);
    }

    const citations = data.citations || [];
    if (citations.length) {
      const card = el("div", "vp-citations");
      card.appendChild(el("div", "vp-card-head", `参考资料（${citations.length} 条，可点击溯源）`));
      for (const item of citations) {
        const meta = `${item.kind} · ${item.date} · ${item.org || "未知机构"}`;
        const label = `${meta} · ${item.title}`;
        if (item.url) {
          const link = el("a", "vp-cite-link", label);
          link.href = item.url;
          link.target = "_blank";
          link.rel = "noopener";
          card.appendChild(link);
        } else {
          card.appendChild(el("div", "vp-cite-line", label));
        }
      }
      box.appendChild(card);
    }

    const names = { ddm: "股利折现", dcf: "现金流折现" };
    for (const method of ["ddm", "dcf"]) {
      const p = params[method];
      const v = cal[method] || {};
      if (!p) continue;
      const card = el("div", "vp-card");
      const head = el("div", "vp-card-head", names[method]);
      card.appendChild(head);

      card.appendChild(el("div", "vp-params", describeParams(method, p)));
      card.appendChild(el("div", "vp-param-note", PARAM_NOTES[method] || ""));

      const range =
        v.fair_value_low == null
          ? "暂无数据（缺少自由现金流等输入）"
          : `${v.fair_value_low} ~ ${v.fair_value_high} 元`;
      card.appendChild(el("div", "vp-range", `基准合理区间：${range}`));
      card.appendChild(el("div", "vp-margin", `安全边际 ${fmtPct(v.margin_of_safety)}`));
      card.appendChild(renderScenarioRows(data, method));
      if (p.rationale) {
        card.appendChild(el("div", "vp-rationale", p.rationale));
      }
      box.appendChild(card);
    }

    const reinvest = cal.dividend_reinvestment;
    if (reinvest) {
      const ri = el("div", "vp-reinvest");
      ri.appendChild(el("div", "vp-card-head", `10 年股息复投预期（假设股息每年增长 ${pct1(reinvest.growth)}）`));
      const lines = [
        `期初买入 1 股 → 10 年后约 ${reinvest.ending_shares} 股`,
        `期末股价约 ${reinvest.ending_price} 元，持仓市值约 ${reinvest.ending_value} 元`,
        `累计收到股息约 ${reinvest.total_dividend} 元`,
        `总回报约 ${fmtPct(reinvest.total_return)}，年化约 ${fmtPct(reinvest.annualized)}`,
      ];
      for (const line of lines) ri.appendChild(el("div", "vp-ri-line", line));
      box.appendChild(ri);
    }

    if (params.summary) {
      box.appendChild(el("div", "vp-summary", params.summary));
    }
    box.appendChild(
      el("div", "vp-disclaimer", "参数为模型估计，仅作估值敏感性参考，不构成投资建议。")
    );
  }

  function loadValuationParams() {
    const box = document.getElementById("valuationParams");
    getJSON(`/api/stock/${symbol}/valuation-params`)
      .then((data) => renderValuationParams(data))
      .catch(() => {
        box.innerHTML = "";
        box.appendChild(el("p", "muted", "估值参数校准暂不可用"));
      });
  }

  function loadMastersNarrative() {
    getJSON(`/api/stock/${symbol}/masters-narrative`)
      .then((data) => {
        const intelCount = data.intel_count || 0;
        for (const item of data.masters || []) {
          const card = document.getElementById(`master-${item.master}`);
          if (!card) continue;
          let note = card.querySelector(".master-note");
          if (!note) {
            note = el("div", "master-note");
            card.appendChild(note);
          }
          note.className = "master-note";
          note.textContent = item.text || "暂无解读";
          let source = card.querySelector(".master-source");
          if (!source) {
            source = el("span", "master-source");
            card.appendChild(source);
          }
          source.textContent =
            (item.source === "llm" ? "LLM 解读" : "模板解读") +
            (intelCount ? ` · 参考 ${intelCount} 条资料` : "");
        }
      })
      .catch(() => {
        for (const note of document.querySelectorAll("#masters .master-note")) {
          if (note.classList.contains("master-loading")) {
            note.textContent = "解读暂不可用";
          }
        }
      });
  }

  async function render() {
    const [analysis, kline, financials, valHistory] = await Promise.all([
      getJSON(`/api/stock/${symbol}/analysis`),
      getJSON(`/api/stock/${symbol}/kline`),
      getJSON(`/api/stock/${symbol}/financials`),
      getJSON(`/api/stock/${symbol}/valuation-history`),
    ]);
    renderScore(analysis.score);
    renderValuation(analysis.valuation);
    renderMasters(analysis.masters);
    renderReview(analysis.review, analysis.moat, analysis.cycle, analysis.fraud, analysis.delisting);
    valuationHistory = valHistory;
    renderKline(kline);
    renderFinancials(financials);
  }

  loadNarrative();
  loadScoreNarrative();
  loadValuationParams();
  loadValuationNarrative();
  document
    .getElementById("valuationModalClose")
    .addEventListener("click", closeValuationModal);
  document.querySelector(".modal-backdrop").addEventListener("click", closeValuationModal);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeValuationModal();
  });
  render()
    .then(loadMastersNarrative)
    .catch((error) => {
      console.error(error);
    });
})();
