(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";

  const { getJSON } = window.VC;

  // K 线图与财报体检是两条独立的异步链路，谁先到都不丢：都就绪后由 applyForensicMarkers 合流
  let klineChart = null;
  let klineBars = [];
  let forensicData = null;

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

  function markerTooltip(params) {
    const arr = Array.isArray(params) ? params : [params];
    const esc = window.VC.escapeHtml;
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
    const events = arr.filter((p) => p.seriesType === "scatter" && p.data && p.data.meta);
    for (const ev of events) {
      const m = ev.data.meta;
      lines.push(
        '<div class="kl-tt-ev" style="border-left:3px solid ' + m.color + '">'
        + '<div class="kl-tt-ev-head">' + esc(m.title)
        + ' <span class="kl-tt-tone">' + esc(m.toneLabel) + "</span></div>"
        + '<div class="kl-tt-meta">' + esc(m.period) + " · " + esc(m.severity)
        + " · " + esc(m.judgement) + "</div>"
        + (m.detail ? '<div class="kl-tt-detail">' + esc(m.detail) + "</div>" : "")
        + '<div class="kl-tt-hint">' + esc(m.hint || "点击圆点定位到上方对应卡片") + "</div>"
        + "</div>"
      );
    }
    return lines.join("") || "";
  }

  function renderMarkerLegend(result, total) {
    const box = document.getElementById("klineMarkerLegend");
    if (!box) return;
    box.innerHTML = "";
    if (!total) {
      box.appendChild(el("span", "kl-legend-empty", "财报体检加载后，将按报告期在此标注关键点"));
      return;
    }
    const tones = window.VC.MARKER_TONES || {};
    for (const key of Object.keys(tones)) {
      const info = tones[key];
      const count = (result.counts && result.counts[key]) || 0;
      const item = el("span", "kl-legend-item");
      const dot = el("span", "kl-legend-dot");
      if (info) dot.style.background = info.color;
      // 原文通读线索用菱形，与圆形的规则风险点在视觉上区分
      if (info && info.symbol === "diamond") dot.classList.add("is-diamond");
      item.appendChild(dot);
      item.appendChild(el("span", null, (info ? info.label : key) + " " + count));
      box.appendChild(item);
    }
    box.appendChild(
      el("span", "kl-legend-hint", `共 ${total} 个财报关键点（含原文通读线索）· 按报告期定位 · 点击圆点跳转到上方对应卡片`)
    );
  }

  function applyForensicMarkers() {
    if (!klineChart) return;
    const result = window.VC.buildForensicMarkers
      ? window.VC.buildForensicMarkers(forensicData, klineBars)
      : { points: [], counts: {}, earliest: null };
    const points = result.points || [];
    // 按 series 下标合并更新：index 0 保留蜡烛，index 1 只换事件点
    klineChart.setOption({ series: [{}, { data: points }] });
    renderMarkerLegend(result, points.length);
    if (points.length && result.earliest && klineBars.length) {
      // 全历史 K 线很密，把默认视窗拉近到事件区间，否则近年的点会挤成一条线
      const idx = klineBars.findIndex((b) => b.trade_date >= result.earliest);
      if (idx > 0) {
        const start = Math.max(0, Math.min(95, (idx / klineBars.length) * 100 - 4));
        klineChart.setOption({
          dataZoom: [{ start: start, end: 100 }, { start: start, end: 100 }],
        });
      }
    }
  }

  function renderKline(kline) {
    klineBars = kline || [];
    if (!klineChart) {
      klineChart = echarts.init(document.getElementById("kline"));
      klineChart.on("click", function (params) {
        if (params.seriesType !== "scatter" || !params.data) return;
        // 风险点与原文通读线索都带 anchorId；旧数据没有时退回信号 id 定位
        if (params.data.anchorId && window.VC.focusForensicAnchor) {
          window.VC.focusForensicAnchor(params.data.anchorId);
        } else if (params.data.signalId) {
          window.VC.focusForensicSignal(params.data.signalId);
        }
      });
      window.addEventListener("resize", function () {
        if (klineChart) klineChart.resize();
      });
    }
    klineChart.setOption({
      animation: false,
      grid: { left: 56, right: 20, top: 20, bottom: 60 },
      xAxis: { type: "category", data: klineBars.map((b) => b.trade_date) },
      // 第二条隐藏轴专供底部事件带定位，不参与价格刻度，避免干扰蜡烛
      yAxis: [
        { scale: true },
        { min: 0, max: 1, show: false, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20, bottom: 8 }],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        formatter: markerTooltip,
      },
      series: [
        {
          name: "日K",
          type: "candlestick",
          yAxisIndex: 0,
          data: klineBars.map((b) => [b.open, b.close, b.low, b.high]),
        },
        {
          name: "财报关键点",
          type: "scatter",
          xAxisIndex: 0,
          yAxisIndex: 1,
          data: [],
        },
      ],
    }, true);
    applyForensicMarkers();
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

  // 悲观/基准/乐观三档对照：输入相同，只变 g/r 假设。
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
      const g = row.p && row.p.growth != null ? `${(row.p.growth * 100).toFixed(1)}%` : "-";
      const r = row.p && row.p.discount != null ? `${(row.p.discount * 100).toFixed(1)}%` : "-";
      const t = row.p && row.p.terminal_growth != null ? ` · 永续 ${(row.p.terminal_growth * 100).toFixed(1)}%` : "";
      line.appendChild(el("span", "vp-sc-params", `g ${g} · r ${r}${t}`));
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

      const paramParts = [];
      paramParts.push(`增长 g ${(p.growth * 100).toFixed(1)}%`);
      paramParts.push(`折现 r ${(p.discount * 100).toFixed(1)}%`);
      if (p.terminal_growth != null) {
        paramParts.push(`永续 ${(p.terminal_growth * 100).toFixed(1)}%`);
      }
      card.appendChild(el("div", "vp-params", paramParts.join(" · ")));

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
      ri.appendChild(el("div", "vp-card-head", `10 年股息复投预期（按 g ${(reinvest.growth * 100).toFixed(1)}%）`));
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
  loadValuationParams();
  loadValuationNarrative();
  if (window.VC.loadForensics) {
    const forensicsBox = document.getElementById("forensics");
    const onForensics = (data) => {
      if (!data) return;
      forensicData = data;
      applyForensicMarkers();
    };
    window.VC.loadForensics(forensicsBox, symbol).then(onForensics);
    const refreshBtn = document.getElementById("forensicsRefresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshBtn.disabled = true;
        window.VC.loadForensics(forensicsBox, symbol, { refresh: true })
          .then(onForensics)
          .finally(() => {
            refreshBtn.disabled = false;
          });
      });
    }
  }
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
