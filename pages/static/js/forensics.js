/* 财报深度体检渲染组件：report 页与 review 独立页共用。
 * 只做展示，不改写后端结论；所有文本用 textContent 写入，避免资料标题中的特殊字符破坏结构。
 */
(function () {
  const VC = (window.VC = window.VC || {});

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  const LEVELS = {
    high: { label: "高关注", cls: "high" },
    medium: { label: "需关注", cls: "medium" },
    low: { label: "线索级", cls: "low" },
    clear: { label: "未见明显异常", cls: "clear" },
  };
  const SEVERITY = {
    high: { label: "高", cls: "high" },
    medium: { label: "中", cls: "medium" },
    low: { label: "低", cls: "low" },
  };
  const JUDGEMENT = {
    confirmed: { label: "已披露事实", cls: "confirmed" },
    suspicious: { label: "需核查线索", cls: "suspicious" },
    normal: { label: "更可能是正常解释", cls: "normal" },
    unverifiable: { label: "数据不足", cls: "unverifiable" },
  };
  const CONFIDENCE = { high: "高把握", medium: "中等把握", low: "把握有限" };
  const SOURCE = {
    llm: "LLM 逐条剖析",
    template: "客观扫描（未启用 LLM）",
    template_fallback: "客观扫描（模型暂不可用）",
  };

  function chip(text, cls) {
    return el("span", `fx-chip fx-chip-${cls || "plain"}`, text);
  }

  function sectionTitle(text, extra) {
    const row = el("div", "fx-section-title");
    row.appendChild(el("h3", null, text));
    if (extra) row.appendChild(el("span", "fx-section-extra", extra));
    return row;
  }

  function stat(label, value) {
    const box = el("div", "fx-stat");
    box.appendChild(el("div", "fx-stat-value", value));
    box.appendChild(el("div", "fx-stat-label", label));
    return box;
  }

  function fmtNum(value, unit) {
    if (value === null || value === undefined) return "-";
    const n = Number(value);
    if (!isFinite(n)) return String(value);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}${unit || ""}`;
  }

  function safeLink(item) {
    const url = item.url || "";
    if (!/^https?:\/\//i.test(url)) return el("span", "fx-cite-line", item.title || "");
    const a = el("a", "fx-cite-link", item.title || url);
    a.setAttribute("href", url);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    return a;
  }

  function renderVerdict(data) {
    const scan = data.scan || {};
    const summary = scan.summary || {};
    const window_ = scan.window || {};
    const verdict = data.verdict || {};
    const level = LEVELS[verdict.level] || LEVELS.low;

    const card = el("div", `fx-verdict fx-level-${level.cls}`);
    const head = el("div", "fx-verdict-head");
    head.appendChild(chip(level.label, level.cls));
    head.appendChild(chip(SOURCE[data.source] || "客观扫描", "source"));
    head.appendChild(
      el("span", "fx-window", `结论窗口 ${window_.conclusion_from || "-"} ~ ${window_.conclusion_to || "-"}`)
    );
    card.appendChild(head);
    card.appendChild(el("div", "fx-headline", verdict.headline || "财报深度体检"));
    card.appendChild(el("p", "fx-essence", verdict.essence || ""));

    const stats = el("div", "fx-stats");
    stats.appendChild(stat("风险点", summary.signal_count ?? "-"));
    stats.appendChild(stat("高等级", summary.high ?? "-"));
    stats.appendChild(stat("异常波动", summary.anomaly_count ?? "-"));
    stats.appendChild(stat("扫描财报期数", scan.periods ? scan.periods.count : "-"));
    stats.appendChild(stat("趋势合并", summary.consolidated ?? 0));
    card.appendChild(stats);
    return card;
  }

  function renderFacts(signal) {
    const wrap = el("div", "fx-facts");
    wrap.appendChild(el("div", "fx-block-label", "客观证据（代码计算）"));
    const grid = el("div", "fx-fact-grid");
    for (const f of signal.facts || []) {
      const cell = el("div", "fx-fact");
      cell.appendChild(el("div", "fx-fact-label", f.label));
      cell.appendChild(el("div", "fx-fact-value", f.value));
      grid.appendChild(cell);
    }
    wrap.appendChild(grid);
    if (signal.trigger) wrap.appendChild(el("div", "fx-trigger", `触发规则：${signal.trigger}`));
    if (signal.description) wrap.appendChild(el("p", "fx-description", signal.description));
    return wrap;
  }

  function renderAnalysis(analysis) {
    const box = el("div", "fx-analysis");
    if (!analysis.analyzed) {
      box.appendChild(
        el("p", "fx-no-llm", "当前未生成模型剖析。以下为客观证据，请结合公告原文自行核验。")
      );
      return box;
    }
    const blocks = [
      ["实质是什么", analysis.substance, "substance"],
      ["更可能的合理解释", analysis.benign, "benign"],
      ["需要警惕的情形（待核查，非结论）", analysis.risk, "risk"],
      ["建议核查", analysis.action, "action"],
    ];
    for (const [label, text, cls] of blocks) {
      if (!text) continue;
      const block = el("div", `fx-ana-block fx-ana-${cls}`);
      block.appendChild(el("div", "fx-ana-label", label));
      block.appendChild(el("p", "fx-ana-text", text));
      box.appendChild(block);
    }
    const j = JUDGEMENT[analysis.judgement] || JUDGEMENT.unverifiable;
    const foot = el("div", "fx-ana-foot");
    foot.appendChild(chip(j.label, j.cls));
    foot.appendChild(el("span", "fx-confidence", CONFIDENCE[analysis.confidence] || ""));
    box.appendChild(foot);
    return box;
  }

  function renderOccurrences(signal) {
    const list = signal.occurrences || [];
    if (!list.length) return null;
    const details = el("details", "fx-details");
    details.appendChild(el("summary", null, `逐期明细（${list.length} 期）`));
    const table = el("table", "fx-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const t of ["报告期", "等级", "关键数值"]) hr.appendChild(el("th", null, t));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const o of list) {
      const tr = el("tr");
      tr.appendChild(el("td", null, o.period));
      tr.appendChild(el("td", null, (SEVERITY[o.severity] || {}).label || o.severity || ""));
      const facts = (o.facts || []).map((f) => `${f.label} ${f.value}`).join("；");
      tr.appendChild(el("td", null, facts));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    details.appendChild(table);
    return details;
  }

  function renderSignals(data) {
    const wrap = el("div", "fx-signals-wrap");
    const signals = (data.scan && data.scan.signals) || [];
    const analyses = data.analyses || [];
    const byId = {};
    for (const a of analyses) byId[a.id] = a;

    wrap.appendChild(sectionTitle("风险点逐条剖析", `共 ${signals.length} 条`));
    if (!signals.length) {
      wrap.appendChild(
        el("p", "fx-empty", "在现有数据与阈值下未检出风险点。请留意下方「未执行的检查」——未检出不等于不存在。")
      );
      return wrap;
    }
    const list = el("div", "fx-signal-list");
    signals.forEach((signal, index) => {
      const analysis = byId[signal.id] || {
        id: signal.id, analyzed: false, judgement: "unverifiable", confidence: "low",
        substance: signal.description, benign: "", risk: "", action: "",
      };
      const sev = SEVERITY[signal.severity] || SEVERITY.low;
      const details = el("details", `fx-signal fx-sev-${sev.cls}`);
      if (index < 3 || signal.severity === "high") details.setAttribute("open", "open");

      const summary = el("summary", "fx-sig-head");
      summary.appendChild(chip(sev.label, sev.cls));
      summary.appendChild(el("span", "fx-sig-title", signal.title));
      summary.appendChild(el("span", "fx-sig-cat", signal.category));
      summary.appendChild(el("span", "fx-sig-period", signal.period));
      details.appendChild(summary);

      const body = el("div", "fx-sig-body");
      body.appendChild(renderFacts(signal));
      body.appendChild(renderAnalysis(analysis));
      const occ = renderOccurrences(signal);
      if (occ) body.appendChild(occ);
      details.appendChild(body);
      list.appendChild(details);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderAnomalies(data) {
    const rows = (data.scan && data.scan.anomalies) || [];
    const wrap = el("div", "fx-anomalies");
    wrap.appendChild(sectionTitle("异常波动归因", `共 ${rows.length} 项`));
    if (!rows.length) {
      wrap.appendChild(el("p", "fx-empty", "结论窗口内未触发异常波动规则。"));
      return wrap;
    }
    const details = el("details", "fx-details");
    details.appendChild(el("summary", null, `展开异常波动明细（${rows.length} 项）`));
    const table = el("table", "fx-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const t of ["指标", "报告期", "本期", "上年同期", "变化", "判定阈值", "说明"]) {
      hr.appendChild(el("th", null, t));
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const r of rows) {
      const tr = el("tr");
      tr.appendChild(el("td", null, r.metric));
      tr.appendChild(el("td", null, r.period));
      tr.appendChild(el("td", null, fmtNum(r.current, r.unit)));
      tr.appendChild(el("td", null, fmtNum(r.previous, r.unit)));
      const change = el("td", `fx-delta fx-delta-${r.direction || "flat"}`, fmtNum(r.change, r.unit));
      tr.appendChild(change);
      tr.appendChild(el("td", null, r.threshold));
      tr.appendChild(el("td", null, r.note || ""));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    details.appendChild(table);
    wrap.appendChild(details);
    return wrap;
  }

  function renderCrossChecks(data) {
    const wrap = el("div", "fx-cross");
    wrap.appendChild(sectionTitle("交叉印证", "跨信号 / 跨数据源关联发现"));
    const list = el("ul", "fx-cross-list");
    for (const c of data.cross_checks || []) list.appendChild(el("li", null, c));
    if (!(data.cross_checks || []).length) {
      wrap.appendChild(el("p", "fx-empty", "未生成跨信号关联分析。"));
    } else {
      wrap.appendChild(list);
    }
    return wrap;
  }

  function renderCoverage(data) {
    const wrap = el("div", "fx-coverage");
    wrap.appendChild(sectionTitle("数据覆盖与局限", "「未检出」不等于「没问题」"));
    const coverage = data.coverage || {};
    const performed = coverage.performed || [];
    const skipped = coverage.skipped || [];

    const done = el("div", "fx-coverage-block");
    done.appendChild(el("div", "fx-block-label", `已执行检查（${performed.length}）`));
    const chips = el("div", "fx-chip-row");
    for (const p of performed) chips.appendChild(chip(p, "ok"));
    done.appendChild(chips);
    wrap.appendChild(done);

    if (skipped.length) {
      const miss = el("div", "fx-coverage-block");
      miss.appendChild(el("div", "fx-block-label", `因数据缺失未执行的检查（${skipped.length}）`));
      const ul = el("ul", "fx-skip-list");
      for (const s of skipped) {
        ul.appendChild(el("li", null, `${s.check}：${s.reason}`));
      }
      miss.appendChild(ul);
      wrap.appendChild(miss);
    }

    if ((data.caveats || []).length) {
      const cav = el("div", "fx-coverage-block");
      cav.appendChild(el("div", "fx-block-label", "分析边界与免责"));
      const ul = el("ul", "fx-caveat-list");
      for (const c of data.caveats) ul.appendChild(el("li", null, c));
      cav.appendChild(ul);
      wrap.appendChild(cav);
    }
    return wrap;
  }

  function renderCitations(data) {
    const wrap = el("div", "fx-citations");
    const items = data.citations || [];
    wrap.appendChild(sectionTitle("参考资料", `可点击溯源 · ${items.length} 条`));
    if (!items.length) {
      wrap.appendChild(el("p", "fx-empty", "暂无可溯源资料。"));
      return wrap;
    }
    const list = el("div", "fx-cite-list");
    for (const item of items) {
      const row = el("div", "fx-cite-row");
      row.appendChild(chip(item.kind, "cite"));
      if (item.date) row.appendChild(el("span", "fx-cite-date", item.date));
      if (item.org) row.appendChild(el("span", "fx-cite-org", item.org));
      row.appendChild(safeLink(item));
      list.appendChild(row);
    }
    wrap.appendChild(list);
    return wrap;
  }

  // 阈值可调：把本次实际生效的配置展示出来，方便确认 config/forensics.toml 已生效。
  const THRESHOLD_LABELS = {
    lookback_periods: "结论窗口（报告期）",
    recent_years: "事件证据年限",
    max_signals: "送入 LLM 的风险点上限",
    max_anomalies: "送入 LLM 的波动上限",
    mad_multiplier: "MAD 离群倍数",
    mad_min_samples: "离群判定最少样本",
    rate_require_outlier: "需历史离群的率类指标",
    divergence_revenue_yoy_pct: "增收不增利·营收同比 >",
    divergence_profit_yoy_pct: "增收不增利·净利同比 <",
    profit_growth_gap_pp: "利润/营收增速差 >",
    deducted_gap_pct: "非经常性损益占比 >",
    deducted_gap_high_pct: "非经常性损益升级 high >",
    revenue_yoy_swing_pp: "营收增速骤变 >",
    gross_margin_pp: "毛利率异动 >",
    net_margin_pp: "净利率异动 >",
    roe_pp: "ROE 异动 >",
    debt_ratio_pp: "负债率异动 >",
    roe_drop_high_pp: "ROE 降幅升级 high <",
    ocf_profit_ratio: "经营现金流/净利 <",
    ocf_consecutive_periods: "现金质量连续期数",
    expense_ratio_pp: "费用率异动 >",
    expense_cluster_min_items: "同源科目合并门限",
    forecast_deviation: "预告偏离 >",
    forecast_amount_tolerance_pct: "预告金额容差",
    forecast_yoy_tolerance_pp: "预告同比容差",
    insider_sell_count: "内部人减持笔数 ≥",
    pledge_ratio_pct: "质押比例 >",
    market_profit_yoy_up_pct: "市场反应·净利增 >",
    market_return_down_pct: "市场反应·股价跌 <",
    market_profit_yoy_down_pct: "市场反应·净利降 <",
    market_return_up_pct: "市场反应·股价涨 >",
    price_min_bars: "K 线最少根数",
    price_reaction_periods: "回溯报告期数",
    price_window_days: "披露日前后交易日",
    price_publish_window_days: "披露日搜索天数",
    external_scan_chars: "第三方正文匹配字数",
    external_excerpt_chars: "第三方摘录字数",
    consolidate_min_occurrences: "趋势聚合门限",
    loss_lookback_periods: "亏损回看期数",
    loss_min_count: "亏损期数 ≥",
    risk_high_min_high: "总体 high 门槛",
    risk_medium_min_medium: "总体 medium 门槛",
  };

  function renderThresholds(data) {
    const thresholds = data.thresholds;
    if (!thresholds) return null;
    const wrap = el("div", "fx-thresholds");
    const details = el("details", "fx-details");
    details.appendChild(el("summary", null, "本次生效的触发阈值（可在 config/forensics.toml 调整）"));
    const note = el(
      "p",
      "fx-description",
      `配置文件：config/forensics.toml · 指纹 ${data.thresholds_fingerprint || "-"} · `
        + "调大数值＝触发更少；改完自动重算，无需清缓存。"
    );
    details.appendChild(note);
    const table = el("table", "fx-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const t of ["参数", "当前值"]) hr.appendChild(el("th", null, t));
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const key of Object.keys(thresholds)) {
      if (["risk_title_keywords", "negative_view_keywords", "regulatory_severe_categories"].includes(key)) continue;
      const value = thresholds[key];
      const shown = Array.isArray(value) ? (value.length ? value.join("、") : "（未启用）") : String(value);
      const tr = el("tr");
      tr.appendChild(el("td", null, THRESHOLD_LABELS[key] || key));
      tr.appendChild(el("td", null, shown));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    details.appendChild(table);
    wrap.appendChild(details);
    return wrap;
  }

  VC.renderForensics = function renderForensics(container, data) {
    if (!container) return;
    container.innerHTML = "";
    if (!data || data.error) {
      container.appendChild(el("p", "fx-error", (data && data.error) || "财报深度体检数据不可用"));
      return;
    }
    const wrap = el("div", "fx-wrap");
    wrap.appendChild(renderVerdict(data));
    wrap.appendChild(renderSignals(data));
    wrap.appendChild(renderAnomalies(data));
    wrap.appendChild(renderCrossChecks(data));
    wrap.appendChild(renderCoverage(data));
    const thresholds = renderThresholds(data);
    if (thresholds) wrap.appendChild(thresholds);
    wrap.appendChild(renderCitations(data));
    wrap.appendChild(
      el(
        "div",
        "fx-footer",
        `生成时间 ${data.generated_at || "-"} · 事实由代码确定性计算，判断由模型辅助，不构成审计意见或投资建议。`
      )
    );
    container.appendChild(wrap);
  };

  VC.loadForensics = function loadForensics(container, symbol, options) {
    const opts = options || {};
    if (!container) return Promise.resolve(null);
    container.innerHTML = "";
    container.appendChild(
      el("p", "fx-loading", "正在生成财报深度体检…（需扫描多期财报、交叉外部资料并逐条剖析，首次约需数十秒）")
    );
    const query = opts.refresh ? "?refresh=true" : "";
    return VC.getJSON(`/api/stock/${encodeURIComponent(symbol)}/financial-forensics${query}`)
      .then((data) => {
        VC.renderForensics(container, data);
        return data;
      })
      .catch((error) => {
        container.innerHTML = "";
        const msg = error && error.message ? error.message : String(error);
        container.appendChild(el("p", "fx-error", `财报深度体检暂时不可用：${msg}`));
        return null;
      });
  };
})();
