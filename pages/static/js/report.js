(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";

  const { getJSON } = window.VC;

  // K 线与估值叠加折线共用一条时间轴：两路数据都就绪后合成
  let klineChart = null;
  let klineBars = [];
  let valuationHistory = null;
  let analysisValuation = {};
  // 校准面板的 FCF 提示要回填 analysis 数据，两个接口谁先返回都要能正确渲染。
  let valuationParamsData = null;

  // 生成「计算器」按钮：回填值由各口径的 inputs/assumptions 拼装。
  function calcButton(method, data) {
    if (!window.ValuationCalculator) return null;
    const btn = el("button", "val-calc-btn", "计算器");
    btn.type = "button";
    btn.title = "自定义输入重算该口径";
    btn.addEventListener("click", () => window.ValuationCalculator.open(method, data));
    return btn;
  }

  // 校准面板的 DDM/DCF：参数用校准后的 p，输入值优先取 analysis 里同口径的 inputs。
  function calcDataFromCalibration(method, p, v, data) {
    // 校准 API 的 calibrated[method].inputs 已含 DPS/FCF/股本，最可靠；analysis 作为补充。
    const item = analysisValuation[method] || {};
    const calInp = (v && v.inputs) || {};
    const anaInp = (item.inputs) || {};
    const base = calcDataFromResult(item);
    base.growth = p && p.growth != null ? p.growth : base.growth;
    base.discount = p && p.discount != null ? p.discount : base.discount;
    base.terminalGrowth = p && p.terminal_growth != null ? p.terminal_growth : base.terminalGrowth;
    base.price = (v && v.current_price) || base.price;
    if (method === "ddm") {
      base.dps = calInp.dividend_per_share != null ? calInp.dividend_per_share
        : (anaInp.dividend_per_share != null ? anaInp.dividend_per_share : base.dps);
    }
    if (method === "dcf") {
      base.fcf = calInp.fcf != null ? calInp.fcf : (anaInp.fcf != null ? anaInp.fcf : base.fcf);
      base.shares = calInp.shares != null ? calInp.shares : (anaInp.shares != null ? anaInp.shares : base.shares);
    }
    if (method === "adjusted_fcf") {
      base.shares = calInp.shares != null ? calInp.shares : (anaInp.shares != null ? anaInp.shares : base.shares);
      // FCF 分项：校准重算后的 inputs 含 ocf/capex/利息；analysis 结果作为兜底。
      for (const [k, js] of [["ocf", "ocf"], ["capex", "capex"],
                             ["interestIncome", "interest_income"], ["interestExpense", "interest_expense"]]) {
        const val = calInp[js] != null ? calInp[js] : anaInp[js];
        if (val != null) base[k] = val;
      }
      base.fcfSource = calInp.ocf != null ? "derived" : (anaInp.base_source || base.fcfSource);
    }
    if (method === "fcff") {
      // 桥接项全部来自校准重算后的 inputs（与 analysis 同源），保证计算器与卡片一致。
      for (const [js, key] of [
        ["ocf", "ocf"], ["capex", "capex"], ["interestExpense", "interest_expense"],
        ["cash", "cash"],
        ["fairValueAssets", "fair_value_assets"], ["equityAssets", "equity_assets"],
        ["marketableSecurities", "marketable_securities"],
        ["nonoperatingInvest", "nonoperating_invest"], ["debt", "debt"],
        ["minorityEquity", "minority_equity"], ["revenue", "revenue"], ["shares", "shares"],
      ]) {
        const val = calInp[key] != null ? calInp[key] : anaInp[key];
        if (val != null) base[js] = val;
      }
      // 若校准结果仍用旧合并键，回填到新三档（优先新键，旧键兜底）。
      if (base.fairValueAssets == null && calInp.marketable_securities != null) base.fairValueAssets = calInp.marketable_securities;
      if (base.equityAssets == null && calInp.nonoperating_invest != null) base.equityAssets = calInp.nonoperating_invest;
    }
    return base;
  }

  function calcDataFromResult(item) {
    const inp = (item && item.inputs) || {};
    const asm = (item && item.assumptions) || {};
    return {
      eps: inp.eps, bps: inp.bps,
      peMedian: asm.median_pe, peLow: inp.pe_low, peHigh: inp.pe_high,
      dps: inp.dividend_per_share,
      fcf: inp.fcf, shares: inp.shares,
      fcfSource: inp.base_source || "derived",
      ocf: inp.ocf != null ? inp.ocf : (inp.sources || {}).ocf,
      capex: inp.capex != null ? inp.capex : (inp.sources || {}).capex,
      interestIncome: inp.interest_income != null ? inp.interest_income : (inp.sources || {}).interest_income,
      interestExpense: inp.interest_expense != null ? inp.interest_expense : (inp.sources || {}).interest_expense,
      netProfit: (inp.sources || {}).net_profit,
      inventory: (inp.working_capital_changes || {}).inventory,
      receivable: (inp.working_capital_changes || {}).receivable,
      payable: (inp.working_capital_changes || {}).payable,
      prepaid: (inp.working_capital_changes || {}).prepaid,
      contractLiability: (inp.working_capital_changes || {}).contract_liability,
      workingCapitalAlert: (item && item.assumptions || {}).working_capital_alert,
      growth: asm.growth, discount: asm.discount,
      terminalGrowth: asm.terminal_growth, years: asm.years,
      price: item && item.current_price,
      cash: inp.cash,
      // 三档桥接：现金/存款按面值；公允价值类与权益类分别折价。
      fairValueAssets: inp.fair_value_assets != null ? inp.fair_value_assets : inp.marketable_securities,
      equityAssets: inp.equity_assets != null ? inp.equity_assets : inp.nonoperating_invest,
      marketableSecurities: inp.marketable_securities,
      nonoperatingInvest: inp.nonoperating_invest,
      debt: inp.debt,
      minorityEquity: inp.minority_equity,
      revenue: inp.revenue,
      excessCashRatio: asm.excess_cash_ratio,
      fairValueDiscount: asm.fair_value_discount != null ? asm.fair_value_discount : asm.nonoperating_discount,
      equityDiscount: asm.equity_discount != null ? asm.equity_discount : asm.nonoperating_discount,
      nonoperatingDiscount: asm.nonoperating_discount,
    };
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
    growth: "预测增长率",
    discount: "折现率",
    years: "预测年数",
    terminal_growth: "永续增长率",
    median_pe: "历史PE中位数",
    pe_percentile: "当前PE分位",
    current_pe: "当前PE",
    pe_low: "PE 20%分位",
    pe_high: "PE 80%分位",
    eps: "EPS",
    bps: "BPS",
    dividend_per_share: "每股股利",
    net_profit: "净利润",
    ocf: "经营现金流",
    capex: "资本开支",
    interest_income: "利息收入",
    interest_expense: "利息支出",
    fcf: "自由现金流",
    fcff: "企业自由现金流",
    cash: "货币资金",
    marketable_securities: "交易性金融资产/存款",
    nonoperating_invest: "非经营投资（原值）",
    debt: "有息负债",
    minority_equity: "少数股东权益",
    revenue: "营业收入",
    enterprise_value: "企业价值 EV",
    equity_value: "股权价值",
    excess_cash: "超额现金",
    nonoperating_assets_discounted: "非经营投资（折价后）",
    shares: "总股本",
    prepaid: "预付款项增加",
    contract_liability: "合同负债（经销商预付款）增加",
  };

  const SOURCE_LABELS = {
    derived: "标准公式推导",
    direct: "直接输入FCF",
  };

  const SKIP_PARAM_KEYS = new Set([
    "formula", "param_source", "sources", "base_source",
    "fcf_source", "working_capital_basis", "working_capital_alert",
  ]);
  const RATE_KEYS = new Set(["growth", "discount", "terminal_growth", "pe_percentile"]);
  const PE_KEYS = new Set(["median_pe", "current_pe", "pe_low", "pe_high"]);
  const PER_SHARE_KEYS = new Set(["eps", "bps", "dividend_per_share"]);
  const MONEY_KEYS = new Set([
    "fcf", "fcff", "ocf", "net_profit", "capex", "interest_income", "interest_expense",
    "cash", "marketable_securities", "nonoperating_invest", "debt", "minority_equity",
    "revenue", "enterprise_value", "equity_value", "excess_cash", "nonoperating_assets_discounted",
  ]);
  const RATE_KEYS_EXTRA = new Set(["excess_cash_ratio", "nonoperating_discount"]);

  function fmtParamValue(key, value) {
    if (value == null) return "数据缺失";
    if (typeof value === "object") return "见口径说明";
    if (key === "base_source") return SOURCE_LABELS[value] || value;
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    if (RATE_KEYS.has(key) || RATE_KEYS_EXTRA.has(key)) return `${(n * 100).toFixed(1)}%`;
    if (key === "shares") return `${(n / 1e8).toFixed(2)}亿股`;
    if (MONEY_KEYS.has(key)) {
      if (Math.abs(n) >= 1e7) return `${(n / 1e8).toFixed(2)}亿`;
      return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    }
    if (PE_KEYS.has(key)) return n.toFixed(2);
    if (PER_SHARE_KEYS.has(key)) return n.toFixed(2);
    return Number.isInteger(n) ? String(n) : n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function humanizeFormula(text) {
    return String(text || "").replace(/sqrt\s*\(/gi, "√(");
  }

  function renderParams(item) {
    const chips = [];
    const seen = new Set();
    const collect = (obj) => {
      for (const [key, value] of Object.entries(obj || {})) {
        if (SKIP_PARAM_KEYS.has(key) || seen.has(key) || value == null || typeof value === "object") continue;
        seen.add(key);
        chips.push(el("span", "val-param", `${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`));
      }
    };
    collect(item.assumptions);
    collect(item.inputs);
    if (!chips.length) return null;
    const wrap = el("div", "val-params");
    wrap.appendChild(el("span", "val-param-label", "参数"));
    chips.forEach((chip) => wrap.appendChild(chip));
    return wrap;
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
    const display = {
      relative: { title: "相对估值", sub: "自身历史 PE 分位" },
      graham: { title: "格雷厄姆", sub: "防御型估值" },
    };
    // 保存全部口径供计算器回填；DDM/DCF/自由现金流折现都在下方校准区展示，避免重复。
    analysisValuation = {};
    for (const item of (valuation || [])) analysisValuation[item.method] = item;
    for (const item of (valuation || []).filter(
      (v) => v.method === "relative" || v.method === "graham"
    )) {
      const meta = display[item.method] || { title: item.method, sub: "" };
      const block = el("div", "val-block");
      block.id = "val-" + item.method;

      const head = el("div", "val-head");
      const title = el("div", "val-title");
      title.appendChild(el("div", "val-title-main", meta.title));
      if (meta.sub) title.appendChild(el("div", "val-title-sub", meta.sub));
      const actions = el("div", "val-actions");
      const btn = calcButton(item.method, calcDataFromResult(item));
      if (btn) actions.appendChild(btn);
      const help = el("button", "val-help", "?");
      help.type = "button";
      help.title = "查看该估值模型的历史与说明";
      help.setAttribute("aria-label", "查看估值模型说明");
      help.addEventListener("click", () => openValuationModal(item.method));
      actions.appendChild(help);
      head.appendChild(title);
      head.appendChild(actions);
      block.appendChild(head);

      const row = el("div", "val-row");
      const rangeWrap = el("div", "val-range-wrap");
      rangeWrap.appendChild(el("span", "val-range-label", "合理价区间"));
      rangeWrap.appendChild(el(
        "div",
        "val-range",
        item.fair_value_low == null
          ? "暂无数据"
          : `${item.fair_value_low} ~ ${item.fair_value_high} 元`
      ));
      const marginClass = item.margin_of_safety != null && item.margin_of_safety < 0
        ? "val-margin val-margin-negative" : "val-margin";
      const margin = el("div", marginClass, `安全边际 ${fmtPct(item.margin_of_safety)}`);
      row.appendChild(rangeWrap);
      row.appendChild(margin);
      block.appendChild(row);

      if (item.assumptions && item.assumptions.formula) {
        block.appendChild(el("div", "val-formula", humanizeFormula(item.assumptions.formula)));
      }
      const alerts = [item.assumptions && item.assumptions.fcf_source,
                      item.assumptions && item.assumptions.working_capital_alert]
        .filter(Boolean);
      if (alerts.length) block.appendChild(el("div", "val-alert", alerts.join("　｜　")));
      const params = renderParams(item);
      if (params) block.appendChild(params);
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

  // K 线叠加三条估值折线：与日 K 共用同一交易日序列。PE/PB 优先官方日频，
  // 缺口及港股按财报 EPS/BPS 推导；ROE 取年报 as-of。三条线各自独立 Y 轴。
  const OVERLAY_COLORS = { pe: "#7c3aed", pb: "#0891b2", roe: "#d97706" };
  const OVERLAY_LABELS = { pe: "市盈率 PE", pb: "市净率 PB", roe: "ROE" };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function overlayTooltip(params) {
    const arr = Array.isArray(params) ? params : [params];
    const index = arr.length ? arr[0].dataIndex : null;
    if (index == null || index < 0) return "";
    const bar = klineBars[index];
    const lines = [];
    lines.push('<div class="kl-tt-date">' + esc(bar ? bar.trade_date : "") + "</div>");
    if (bar) {
      lines.push('<div class="kl-tt-ohlc">开 ' + esc(bar.open) + "　收 " + esc(bar.close)
        + "　低 " + esc(bar.low) + "　高 " + esc(bar.high) + "</div>");
    }
    const hist = valuationHistory || {};
    for (const key of ["pe", "pb", "roe"]) {
      const series = hist[key] || [];
      const raw = series[index];
      const value = raw == null ? "—" : Number(raw).toFixed(2) + (key === "roe" ? "%" : "");
      const source = key === "roe" ? "年报" : (hist[key + "_source"] || [])[index];
      const period = (hist[key + "_period"] || [])[index];
      const meta = [source, period ? "报告期 " + period : null].filter(Boolean).join(" · ");
      lines.push(
        '<div class="kl-tt-meta" style="border-left:3px solid ' + OVERLAY_COLORS[key] + ';padding-left:6px">'
        + OVERLAY_LABELS[key] + " " + esc(value)
        + (meta ? ' <span style="color:#94a3b8">（' + esc(meta) + "）</span>" : "")
        + "</div>"
      );
    }
    if (hist.fx && hist.fx.is_fallback) {
      lines.push('<div class="kl-tt-hint">港股汇率使用配置回退值，仅用于图形叠加</div>');
    }
    return lines.join("");
  }

  function overlayData(key) {
    const data = valuationHistory && valuationHistory[key];
    return Array.isArray(data) ? data : [];
  }

  function applyOverlay() {
    if (!klineChart || !valuationHistory) return;
    klineChart.setOption({
      series: [
        {},
        { data: overlayData("pe") },
        { data: overlayData("pb") },
        { data: overlayData("roe") },
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
        { name: "市盈率 PE", type: "line", yAxisIndex: 1, showSymbol: false,
          lineStyle: { width: 1.5, color: OVERLAY_COLORS.pe }, itemStyle: { color: OVERLAY_COLORS.pe }, data: [] },
        { name: "市净率 PB", type: "line", yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1.5, color: OVERLAY_COLORS.pb }, itemStyle: { color: OVERLAY_COLORS.pb }, data: [] },
        { name: "ROE", type: "line", yAxisIndex: 3, showSymbol: false,
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

  // 若 fcff 的 LLM 折现率被利率下限抬高，参数行补一句实际执行值，避免显示与计算不一致。
  function floorNote(cal) {
    const asm = (cal && cal.assumptions) || {};
    if (!asm.discount_floor_applied || asm.discount == null) return "";
    return ` · 低于利率下限，实际按 ${pct1(asm.discount)} 执行`;
  }

  // 把模型的 g/r 翻译成用户能直接读懂的中文，并明确增长率适用的年限。
  function describeParams(method, p, cal) {
    if (!p) return "";
    if (method === "ddm") {
      return `股息长期增长率 ${pct1(p.growth)}（长期永续） · 折现率 ${pct1(p.discount)}`;
    }
    const label = method === "fcff" ? "企业自由现金流"
      : method === "adjusted_fcf" ? "自由现金流" : "现金流";
    const discountLabel = method === "fcff" ? "WACC" : "折现率";
    let text = `前 10 年${label}增长率 ${pct1(p.growth)} · 10 年后永续增长率 ${pct1(p.terminal_growth)} · ${discountLabel} ${pct1(p.discount)}`;
    if (method === "fcff") {
      text += floorNote(cal);
      const bits = [];
      if (p.fair_value_discount != null) bits.push(`公允价值类 ${pct1(p.fair_value_discount)}`);
      if (p.equity_discount != null) bits.push(`权益类 ${pct1(p.equity_discount)}`);
      if (p.excess_cash_ratio != null) bits.push(`最低经营现金 ${pct1(p.excess_cash_ratio)}`);
      if (bits.length) text += ` · 资产折价（按公司质量校准）：${bits.join(" / ")}`;
    }
    return text;
  }

  // 情景行空间有限，用更紧凑的中文表述，年限含义与基准卡片一致。
  function describeScenarioParams(method, p, cal) {
    if (!p) return "-";
    if (method === "ddm") {
      return `永续增长率 ${pct1(p.growth)} · 折现率 ${pct1(p.discount)}`;
    }
    const label = method === "fcff" ? "FCFF" : method === "adjusted_fcf" ? "FCF" : "前10年";
    const discountLabel = method === "fcff" ? "WACC" : "折现率";
    return `${label} ${pct1(p.growth)} · 永续 ${pct1(p.terminal_growth)} · ${discountLabel} ${pct1(p.discount)}`
      + (method === "fcff" ? floorNote(cal) : "");
  }

  const PARAM_NOTES = {
    ddm: "股息增长率按长期永续假设（无固定预测年限）；折现率即要求回报率，越高则合理价越低。合理区间为中枢 ±20%。",
    dcf: "期初现金流 = 经营活动现金流量净额（未扣资本开支、未调整利息收支），与下方标准FCF口径不同；前 10 年按该增长率增长，第 11 年起按永续增长率算终值。折现率即要求回报率，越高则合理价越低。合理区间为中枢 ±20%。",
    adjusted_fcf: "期初股权自由现金流 FCFE = 经营现金流 − 资本开支 − 实付利息；按国际通用惯例，A 股利息收入已含在经营现金流内不重复加回（只作披露与口径核对），港股 OCF 未含损益表利息收支时按财报原文调整。本项目为保守简化，未含净借债。前 10 年按该增长率增长，第 11 年起按永续增长率算终值。增长率与折现率按财报和研报校准，越高越乐观。合理区间为中枢 ±20%。",
    fcff: "FCFF = 经营现金流 + 实付利息 − 资本开支；按 WACC 两阶段折现得企业价值 EV，再桥接为股权价值：EV − 有息负债 + 超额现金 + 非经营投资（按资产质量折价）− 少数股东权益。超额现金留足 2% 营收作为经营周转；WACC 由财报与研报校准，并以市场无风险利率为下限（A股取中国10Y国债、其它取美国10Y国债），取值与来源见下方锚点。净现金公司与重资产公司的结果会明显不同于上面两种股权口径。",
  };

  // 自由现金流折现的取数参数与"现金流质量提示"。
  // 这些数据在主估值结果的 adjusted_fcf 里，校准接口只带三档假设，故从分析数据回填展示。
  function renderFcfQuality() {
    const item = analysisValuation.adjusted_fcf;
    if (!item) return null;
    const asm = item.assumptions || {};
    const inp = item.inputs || {};
    const src = inp.sources || {};
    const wrap = el("div", "vp-fcf-detail");
    wrap.appendChild(el("div", "vp-fcf-title", "现金流质量与取数提示（偏移项不进入公式，仅作风险提醒）"));

    const chips = [];
    const addChip = (key, value) => {
      const missing = value == null || (typeof value === "number" && !Number.isFinite(value));
      // 资本开支/利息确实未取到时要明确显示「数据缺失」，不能让用户误以为是 0。
      if (missing && !["ocf", "capex", "interest_income", "interest_expense"].includes(key)) return;
      chips.push(el("span", "val-param", `${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`));
    };
    addChip("years", asm.years);
    addChip("ocf", inp.ocf);
    addChip("capex", inp.capex);
    // 利息收入已含在经营现金流内、按 0 进公式；有原文披露值时展示披露值并标注口径，
    // 避免用户看到「0」以为漏采。计算器入参仍用公式值 0，二者不可混用。
    const disclosed = inp.interest_income_disclosed != null
      ? inp.interest_income_disclosed : src.interest_income_disclosed;
    if (disclosed != null && Number.isFinite(Number(disclosed)) && Number(disclosed) !== 0) {
      chips.push(el("span", "val-param",
        `利息收入 ${fmtParamValue("interest_income", disclosed)}（已含经营现金流，未加回）`));
    } else {
      addChip("interest_income", inp.interest_income);
    }
    addChip("interest_expense", inp.interest_expense);
    addChip("fcf", inp.fcf);
    addChip("shares", inp.shares);
    addChip("net_profit", src.net_profit);
    // 合同负债/预付/应付/应收/存货是偏移项，只在下方质量提示里展示增减额，不混进取数参数。
    if (chips.length) {
      const row = el("div", "val-params vp-fcf-params");
      row.appendChild(el("span", "val-param-label", "取数参数"));
      chips.forEach((chip) => row.appendChild(chip));
      wrap.appendChild(row);
    }

    if (asm.component_note) {
      wrap.appendChild(el("div", "vp-fcf-source", asm.component_note));
    }
    if (asm.fcf_source) {
      wrap.appendChild(el("div", "vp-fcf-source", asm.fcf_source));
    }
    if (asm.working_capital_alert) {
      wrap.appendChild(el("div", "val-alert", asm.working_capital_alert));
    }
    return wrap.childNodes.length ? wrap : null;
  }

  // FCFF 的 EV→股权桥接明细：让「加回净现金/折价非经营投资」的每一步可核对。
  function renderFcffBridge() {
    const item = analysisValuation.fcff;
    if (!item) return null;
    const asm = item.assumptions || {};
    const inp = item.inputs || {};
    const wrap = el("div", "vp-fcf-detail");
    wrap.appendChild(el("div", "vp-fcf-title", "EV→股权价值桥接与取数提示"));

    const chips = [];
    const addChip = (key) => {
      const value = inp[key];
      if (value == null || (typeof value === "number" && !Number.isFinite(value))) return;
      chips.push(el("span", "val-param", `${PARAM_LABELS[key] || key} ${fmtParamValue(key, value)}`));
    };
    chips.push(el("span", "val-param", `预测年数 ${asm.years != null ? asm.years : "-"}`));
    for (const key of ["ocf", "interest_expense", "capex", "fcff", "cash", "marketable_securities",
                       "nonoperating_invest", "debt", "minority_equity", "revenue",
                       "excess_cash", "nonoperating_assets_discounted", "enterprise_value",
                       "equity_value", "shares"]) {
      addChip(key);
    }
    if (chips.length) {
      const row = el("div", "val-params vp-fcf-params");
      row.appendChild(el("span", "val-param-label", "取数与桥接"));
      chips.forEach((chip) => row.appendChild(chip));
      wrap.appendChild(row);
    }
    if (asm.component_note) wrap.appendChild(el("div", "vp-fcf-source", asm.component_note));
    if (asm.bridge_formula) wrap.appendChild(el("div", "vp-fcf-source", asm.bridge_formula));
    // 桥接折价来源：LLM 按公司资产质量校准的具体取值，便于与普通配置默认值区分。
    const bridgeBits = [];
    if (asm.fair_value_discount != null) bridgeBits.push(`公允价值类按 ${pct1(asm.fair_value_discount)}`);
    if (asm.equity_discount != null) bridgeBits.push(`权益类按 ${pct1(asm.equity_discount)}`);
    if (asm.excess_cash_ratio != null) bridgeBits.push(`最低经营现金 ${pct1(asm.excess_cash_ratio)} 营收`);
    if (bridgeBits.length) {
      wrap.appendChild(el("div", "vp-fcf-source", `资产折价（LLM 结合财报资产构成校准）：${bridgeBits.join("；")}`));
    }
    if (asm.missing_bridge) wrap.appendChild(el("div", "val-alert", asm.missing_bridge));

    // 无风险利率市场锚点：Rf 取值/来源、债务成本 Kd、WACC 下限与本次实际执行值。
    const rf = asm.risk_free;
    if (rf && !rf.missing) {
      const rfBox = el("div", "vp-fcf-detail vp-rf-detail");
      rfBox.appendChild(el("div", "vp-fcf-title", "折现率市场锚点（无风险利率）"));
      const rfChips = [];
      const addRf = (text) => rfChips.push(el("span", "val-param", text));
      if (rf.rf_pct != null) addRf(`${rf.rf_label} ${rf.rf_pct}%`);
      if (rf.rf_date) addRf(`数据日期 ${rf.rf_date}`);
      if (rf.debt_cost_pct != null) addRf(`债务成本 Kd ${rf.debt_cost_pct}%`);
      if (rf.discount_floor_pct != null) addRf(`WACC 下限 ${rf.discount_floor_pct}%`);
      if (asm.discount != null) {
        const pct = (asm.discount * 100).toFixed(2);
        addRf(`本次 WACC ${pct}%${asm.discount_floor_applied ? "（已按下限执行）" : ""}`);
      }
      if (rfChips.length) {
        const rfRow = el("div", "val-params vp-fcf-params");
        rfRow.appendChild(el("span", "val-param-label", "利率锚点"));
        rfChips.forEach((chip) => rfRow.appendChild(chip));
        rfBox.appendChild(rfRow);
      }
      if (rf.rf_source) rfBox.appendChild(el("div", "vp-fcf-source", `数据来源：${rf.rf_source}；债务成本与下限为建模假设，见下方说明。`));
      if (asm.wacc_note) rfBox.appendChild(el("div", "vp-fcf-source", asm.wacc_note));
      if (rf.currency_mismatch) {
        rfBox.appendChild(el("div", "val-alert",
          `币种错配提示：无风险利率以 ${rf.rf_currency} 计、公司现金流以 ${rf.cashflow_currency} 计；按市场规则统一采用境外 10 年期国债，跨市场比较时需注意该口径差异。`));
      }
      wrap.appendChild(rfBox);
    } else if (asm.wacc_note) {
      wrap.appendChild(el("div", "vp-fcf-source", asm.wacc_note));
    }
    return wrap.childNodes.length ? wrap : null;
  }

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
      line.appendChild(el("span", "vp-sc-params", describeScenarioParams(method, row.p, row.v)));
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

    const names = {
      ddm: "股利折现",
      dcf: "现金流折现",
      adjusted_fcf: "自由现金流折现（标准 FCF 口径）",
      fcff: "企业自由现金流折现（FCFF / WACC）",
    };
    for (const method of ["ddm", "dcf", "adjusted_fcf", "fcff"]) {
      const p = params[method];
      const v = cal[method] || {};
      if (!p) continue;
      const card = el("div", "vp-card");
      const head = el("div", "vp-card-head");
      head.appendChild(el("span", "vp-card-title", names[method]));
      const calc = calcButton(method, calcDataFromCalibration(method, p, v, data));
      const actions = el("div", "vp-card-actions");
      if (calc) actions.appendChild(calc);
      const help = el("button", "val-help", "?");
      help.type = "button";
      help.title = "查看该估值模型的说明";
      help.setAttribute("aria-label", "查看估值模型说明");
      help.addEventListener("click", () => openValuationModal(method));
      actions.appendChild(help);
      head.appendChild(actions);
      card.appendChild(head);

      card.appendChild(el("div", "vp-params", describeParams(method, p, v)));
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
      if (method === "adjusted_fcf") {
        const fcfDetail = renderFcfQuality();
        if (fcfDetail) card.appendChild(fcfDetail);
      }
      if (method === "fcff") {
        const bridge = renderFcffBridge();
        if (bridge) card.appendChild(bridge);
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
      .then((data) => {
        valuationParamsData = data;
        renderValuationParams(data);
      })
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
    // 若校准接口先于 analysis 返回，此时 analysisValuation 才就绪，补渲染一次带上取数提示。
    if (valuationParamsData) renderValuationParams(valuationParamsData);
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
