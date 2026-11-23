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

  function renderOfficialExplanation(signal, data) {
    // 公司原文证据：优先取 signal 上挂载的，兼容顶层 official_explanations 映射
    let items = signal.official_explanation;
    if (!items && data && data.official_explanations) {
      items = data.official_explanations[signal.id];
    }
    const wrap = el("div", "fx-official");
    wrap.appendChild(el("div", "fx-block-label", "公司官方说明（财报原文）"));
    if (!items || !items.length) {
      wrap.appendChild(
        el("p", "fx-official-none", "财报原文中未找到针对该类波动的说明，请结合对应报告期公告自行核验。")
      );
      return wrap;
    }
    for (const item of items) {
      const card = el("div", "fx-official-item");
      const meta = el("div", "fx-official-meta");
      const methodLabel = item.method === "llm_mining" ? "智能摘录" : (item.method === "keyword" ? "关键词命中" : "");
      meta.appendChild(el("span", "fx-official-source", item.source || "财报原文"));
      if (methodLabel) meta.appendChild(el("span", "fx-official-method", methodLabel));
      if (item.is_current === true) meta.appendChild(el("span", "fx-official-current", `当期 ${item.report_label || item.period || ""}`.trim()));
      else if (item.period) meta.appendChild(el("span", "fx-official-period", `佐证 ${item.report_label || item.period}`));
      if (item.why) meta.appendChild(el("span", "fx-official-kw", `关联：${item.why}`));
      else if (item.keywords) meta.appendChild(el("span", "fx-official-kw", `命中：${item.keywords}`));
      card.appendChild(meta);
      card.appendChild(el("p", "fx-official-text", item.text || ""));
      wrap.appendChild(card);
    }
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
      // K 线事件点点击后靠这个锚点定位、展开并高亮对应风险卡片
      details.id = signalDomId(signal.id);
      if (index < 3 || signal.severity === "high") details.setAttribute("open", "open");

      const summary = el("summary", "fx-sig-head");
      summary.appendChild(chip(sev.label, sev.cls));
      summary.appendChild(el("span", "fx-sig-title", signal.title));
      summary.appendChild(el("span", "fx-sig-cat", signal.category));
      summary.appendChild(el("span", "fx-sig-period", signal.period));
      details.appendChild(summary);

      const body = el("div", "fx-sig-body");
      body.appendChild(renderFacts(signal));
      body.appendChild(renderOfficialExplanation(signal, data));
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
    // 辅助信息默认折叠，避免长清单挤占正文；重要结论仍在此节内，展开即可查看。
    const details = el("details", "fx-details fx-aux-details");
    details.appendChild(el("summary", null, "展开数据覆盖、未执行检查与免责边界"));
    const coverage = data.coverage || {};
    const performed = coverage.performed || [];
    const skipped = coverage.skipped || [];

    const content = el("div", "fx-aux-content");
    const done = el("div", "fx-coverage-block");
    done.appendChild(el("div", "fx-block-label", `已执行检查（${performed.length}）`));
    const chips = el("div", "fx-chip-row");
    for (const p of performed) chips.appendChild(chip(p, "ok"));
    done.appendChild(chips);
    content.appendChild(done);

    if (skipped.length) {
      const miss = el("div", "fx-coverage-block");
      miss.appendChild(el("div", "fx-block-label", `因数据缺失未执行的检查（${skipped.length}）`));
      const ul = el("ul", "fx-skip-list");
      for (const s of skipped) {
        ul.appendChild(el("li", null, `${s.check}：${s.reason}`));
      }
      miss.appendChild(ul);
      content.appendChild(miss);
    }

    if ((data.caveats || []).length) {
      const cav = el("div", "fx-coverage-block");
      cav.appendChild(el("div", "fx-block-label", "分析边界与免责"));
      const ul = el("ul", "fx-caveat-list");
      for (const c of data.caveats) ul.appendChild(el("li", null, c));
      cav.appendChild(ul);
      content.appendChild(cav);
    }
    details.appendChild(content);
    wrap.appendChild(details);
    return wrap;
  }

  function renderMiningFindings(data) {
    const mining = data.report_mining || {};
    const findings = mining.new_findings || [];
    const summaries = mining.summaries || mining.report_summaries || [];
    if (!findings.length && !summaries.length) return null;
    const wrap = el("div", "fx-mining");
    wrap.id = "fx-mining";
    const sourceDocs = mining.source_docs || [];
    const docCount = sourceDocs.length || (mining.source_doc ? 1 : 0);
    const hasQuarterly = sourceDocs.some((d) => d.doc_type && d.doc_type !== "annual_report");
    const docLabel = (d) => d.report_label || `${d.period || d.title}${d.doc_type === "annual_report" ? " 年报" : ""}`;
    wrap.appendChild(
      sectionTitle(
        "原文通读发现",
        docCount > 1
          ? `通读 ${docCount} 份财报${hasQuarterly ? "（含最新季报）" : ""} · 当下为主，年报佐证`
          : `${mining.source_doc || "财报原文"}${mining.period ? " · " + mining.period : ""}`
      )
    );
    if (docCount > 1) {
      const docRow = el("div", "fx-mining-docs");
      for (const doc of sourceDocs) {
        docRow.appendChild(
          chip(`${doc.is_current ? "当期" : "佐证"} ${docLabel(doc)}`, doc.is_current ? "confirmed" : "plain")
        );
      }
      wrap.appendChild(docRow);
    }
    wrap.appendChild(
      el("p", "fx-mining-note", "模型逐段通读财报原文后发现、但规则扫描未覆盖的线索。仅作提示，每条附原文摘录供核验。")
    );
    if (findings.length) {
      const list = el("div", "fx-mining-list");
      findings.forEach((f, i) => {
        const item = el("div", "fx-mining-item");
        // K 线通读线索点点击后靠这个锚点定位并高亮
        item.id = findingDomId(i);
        const head = el("div", "fx-mining-head");
        head.appendChild(chip(`线索 ${i + 1}`, "suspicious"));
        if (f.is_current) head.appendChild(chip(`当期 ${f.report_label || f.period || ""}`.trim(), "confirmed"));
        else if (f.period) head.appendChild(chip(`佐证 ${f.report_label || f.period}`, "plain"));
        head.appendChild(el("span", "fx-mining-desc", f.description || ""));
        item.appendChild(head);
        if (f.quote) {
          const quote = el("p", "fx-mining-quote", `“${f.quote}”`);
          item.appendChild(quote);
          if (f.quote_kind === "numeric") {
            quote.appendChild(el("span", "fx-quote-kind", "报表数据（数字摘录）"));
          }
        }
        const meta = [];
        if (f.section) meta.push(f.section);
        if (f.source) meta.push(f.source);
        if (meta.length) item.appendChild(el("div", "fx-mining-meta", meta.join(" · ")));
        list.appendChild(item);
      });
      wrap.appendChild(list);
    }
    if (summaries.length) {
      const details = el("details", "fx-aux-details fx-mining-summaries");
      const summary = el("summary", "分段总结原文");
      summary.appendChild(el("span", "fx-aux-hint", `${summaries.length} 段 · 模型对各分段财报的客观归纳`));
      details.appendChild(summary);
      const body = el("div", "fx-aux-content");
      summaries.forEach((s, i) => {
        const row = el("div", "fx-summary-row");
        const head = el("div", "fx-summary-head");
        head.appendChild(chip(`第 ${i + 1} 段`, "plain"));
        if (s.report_label) head.appendChild(el("span", "fx-summary-label", s.report_label));
        if (s.section) head.appendChild(el("span", "fx-summary-section", s.section));
        row.appendChild(head);
        row.appendChild(el("p", "fx-summary-text", s.text || ""));
        body.appendChild(row);
      });
      details.appendChild(body);
      wrap.appendChild(details);
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
    // 参考资料列表较长，默认折叠，标题处已显示条数。
    const details = el("details", "fx-details fx-aux-details");
    details.appendChild(el("summary", null, `展开全部 ${items.length} 条参考资料`));
    const list = el("div", "fx-cite-list");
    for (const item of items) {
      const row = el("div", "fx-cite-row");
      row.appendChild(chip(item.kind, "cite"));
      if (item.date) row.appendChild(el("span", "fx-cite-date", item.date));
      if (item.org) row.appendChild(el("span", "fx-cite-org", item.org));
      row.appendChild(safeLink(item));
      list.appendChild(row);
    }
    details.appendChild(list);
    wrap.appendChild(details);
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
    profit_turn_high_pct: "业绩拐点·平台期均值增速 >",
    inflection_platform_periods: "业绩拐点·平台期长度",
    inflection_platform_floor_pct: "业绩拐点·平台期每期增速 >",
    inflection_min_negative_periods: "业绩拐点·转负后持续期数 >=",
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
    dividend_mad_multiplier: "突击分红·历史离群 >",
    share_capital_growth_pct: "扩充股本·同比增幅 >",
    capex_mad_multiplier: "异常资本支出·历史离群 >",
    goodwill_mad_multiplier: "商誉突增·历史离群 >",
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
      if (["risk_title_keywords", "negative_view_keywords", "regulatory_severe_categories", "capital_action_keywords"].includes(key)) continue;
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
    const mining = renderMiningFindings(data);
    if (mining) wrap.appendChild(mining);
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
      el("p", "fx-loading", "正在生成财报深度体检…（首次需逐段通读财报原文提取证据，约需 1-2 分钟；再次生成命中缓存会很快）")
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

  // ---------- 财报关键点 → K 线底部事件带 ----------
  // 纯数据转换（不碰 DOM），供 report 页把体检结论标到日 K 线图下方，
  // 也便于在 Node 环境做回归测试。颜色遵循 A 股习惯：红=正向/好结果，绿=风险，灰=中性。

  function signalDomId(id) {
    return "fx-signal-" + String(id == null ? "" : id).replace(/[^A-Za-z0-9_-]/g, "");
  }

  function findingDomId(index) {
    // 与 K 线事件点用同一份渲染数据、同一个序号，点击即可反向定位
    return "fx-finding-" + (Number(index) + 1);
  }

  function escapeHtml(value) {
    // 实体名拆开拼接，避免源码里出现会被二次解释的 HTML 实体
    const AMP = "&" + "amp;";
    const LT = "&" + "lt;";
    const GT = "&" + "gt;";
    const QUOT = "&" + "quot;";
    const APOS = "&" + "#39;";
    return String(value == null ? "" : value)
      .replace(/&/g, AMP)
      .replace(/</g, LT)
      .replace(/>/g, GT)
      .replace(/"/g, QUOT)
      .replace(/'/g, APOS);
  }

  const MARKER_TONES = {
    positive: { label: "正向 / 正常解释", color: "#dc2626", y: 0.14 },
    negative: { label: "风险 / 需核查", color: "#16a34a", y: 0.09 },
    neutral: { label: "中性 / 数据不足", color: "#64748b", y: 0.04 },
    // 原文通读发现不是规则信号，也没有模型判定，单独成类：琥珀菱形，
    // 不占用用户约定的「红=正向 / 绿=风险」语义，也不会被误读成结论。
    clue: { label: "原文通读线索", color: "#d97706", y: 0.195, symbol: "diamond" },
  };

  function markerTone(signal, analysis) {
    const judgement = analysis && analysis.judgement;
    // LLM 判定「更可能是正常解释」= 虚惊 / 好结果 → 红
    if (judgement === "normal") return "positive";
    // 已披露事实 / 需核查线索 / 高严重度 → 绿（警示）
    if (judgement === "confirmed" || judgement === "suspicious") return "negative";
    if (signal.severity === "high") return "negative";
    return "neutral";
  }

  function markerDetail(signal, analysis) {
    const parts = [];
    if (analysis && analysis.substance) parts.push(analysis.substance);
    else if (signal.description) parts.push(signal.description);
    if (analysis && analysis.benign) parts.push("合理解释：" + analysis.benign);
    if (analysis && analysis.risk) parts.push("需警惕：" + analysis.risk);
    return parts.join(" ").slice(0, 300);
  }

  function periodDates(period) {
    // 区间型："2023-12-31 年报 ~ 2024-12-31 年报（共 2 期）" → 全部日期，取末次为准
    return String(period || "").match(/20\d{2}-\d{2}-\d{2}/g) || [];
  }

  function anchorTradeDate(kline, reportDate) {
    // x 轴是交易日字符串；报告期常非交易日，取当天或之后最近的一个交易日
    for (const bar of kline) {
      if (bar.trade_date >= reportDate) return bar.trade_date;
    }
    return kline.length ? kline[kline.length - 1].trade_date : null;
  }

  function findingReportDate(f) {
    // 归档文档的 period 常只存年份（如 "2026"），季型写在 report_label 里，
    // 这里还原成报告期末日期，与风险信号的「按报告期定位」口径保持一致。
    const explicit = periodDates(f.period);
    if (explicit.length) return explicit[explicit.length - 1];
    const label = String(f.report_label || "");
    const yearMatch = String(f.period || label).match(/20\d{2}/);
    if (!yearMatch) return null;
    const y = yearMatch[0];
    if (/一季|第一季|Q1/i.test(label)) return y + "-03-31";
    if (/中报|半年|中期|H1/i.test(label)) return y + "-06-30";
    if (/三季|第三季|Q3/i.test(label)) return y + "-09-30";
    return y + "-12-31";
  }

  function findingDetail(f) {
    const parts = [];
    if (f.description) parts.push(f.description);
    if (f.quote) parts.push("原文摘录：" + f.quote);
    return parts.join(" ").slice(0, 360);
  }

  VC.escapeHtml = escapeHtml;
  VC.MARKER_TONES = MARKER_TONES;

  /** 把体检信号 + 原文通读发现一起转成 K 线事件带上的散点。 */
  VC.buildForensicMarkers = function buildForensicMarkers(data, kline) {
    const result = {
      points: [],
      counts: { positive: 0, negative: 0, neutral: 0, clue: 0 },
      earliest: null,
    };
    if (!data || !kline || !kline.length) return result;
    const signals = (data.scan && data.scan.signals) || [];
    // 通读发现是模型逐段读财报后补出的线索，与规则信号并列展示，但不混进红/绿判定
    const findings = ((data.report_mining || {}).new_findings) || [];
    const byId = {};
    for (const analysis of data.analyses || []) byId[analysis.id] = analysis;
    // 同一交易日、同一色调的事件轻微错位，避免多个点完全叠在一起看不见
    const laneCount = {};

    function addPoint(tradeDate, tone, spec) {
      const toneInfo = MARKER_TONES[tone];
      result.counts[tone] += 1;
      if (!result.earliest || tradeDate < result.earliest) result.earliest = tradeDate;
      const laneKey = tradeDate + "|" + tone;
      const lane = laneCount[laneKey] || 0;
      laneCount[laneKey] = lane + 1;
      result.points.push({
        name: spec.name,
        // y 走第二条隐藏轴（0~1）：色调分层 + 同层内错位，全部落在图表底部
        value: [tradeDate, toneInfo.y + Math.min(lane, 4) * 0.017],
        signalId: spec.signalId || null,
        anchorId: spec.anchorId,
        tone: tone,
        symbol: toneInfo.symbol || "circle",
        symbolSize: spec.symbolSize || 10,
        itemStyle: { color: toneInfo.color, borderColor: "#ffffff", borderWidth: 1.5 },
        meta: Object.assign(
          { color: toneInfo.color, toneLabel: toneInfo.label },
          spec.meta
        ),
      });
    }

    for (const signal of signals) {
      const dates = periodDates(signal.period);
      if (!dates.length) continue;
      const tradeDate = anchorTradeDate(kline, dates[dates.length - 1]);
      if (!tradeDate) continue;
      const analysis = byId[signal.id];
      const sev = SEVERITY[signal.severity] || SEVERITY.low;
      const judgement = analysis && JUDGEMENT[analysis.judgement]
        ? JUDGEMENT[analysis.judgement].label
        : "客观扫描";
      addPoint(tradeDate, markerTone(signal, analysis), {
        name: signal.title,
        signalId: signal.id,
        anchorId: signalDomId(signal.id),
        symbolSize: signal.severity === "high" ? 15 : signal.severity === "medium" ? 12 : 9,
        meta: {
          title: signal.title,
          period: signal.period,
          category: signal.category,
          severity: sev.label,
          judgement: judgement,
          detail: markerDetail(signal, analysis),
          hint: "点击圆点定位到上方对应风险点",
        },
      });
    }

    findings.forEach((f, i) => {
      const reportDate = findingReportDate(f);
      if (!reportDate) return;
      const tradeDate = anchorTradeDate(kline, reportDate);
      if (!tradeDate) return;
      addPoint(tradeDate, "clue", {
        name: "原文通读线索 " + (i + 1),
        anchorId: findingDomId(i),
        symbolSize: 11,
        meta: {
          title: "原文通读线索 " + (i + 1),
          period: f.report_label || f.period || "",
          severity: "线索",
          judgement: "规则扫描未覆盖",
          detail: findingDetail(f),
          hint: "点击圆点定位到上方「原文通读发现」",
          findingId: i + 1,
        },
      });
    });
    return result;
  };

  /** 由 K 线事件点反向定位到上方财报体检的对应卡片（风险点或原文通读发现）。 */
  VC.focusForensicAnchor = function focusForensicAnchor(anchorId) {
    if (!anchorId) return false;
    const node = document.getElementById(anchorId);
    if (!node) return false;
    // 自己或任一祖先是折叠的 <details> 时先展开，否则滚过去也看不见内容
    let cur = node;
    while (cur) {
      if (cur.tagName && cur.tagName.toLowerCase() === "details") {
        cur.setAttribute("open", "open");
      }
      cur = cur.parentElement;
    }
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    node.classList.add("fx-signal-flash");
    window.setTimeout(() => node.classList.remove("fx-signal-flash"), 1800);
    return true;
  };

  /** 兼容旧调用：由信号 id 定位到对应风险卡片。 */
  VC.focusForensicSignal = function focusForensicSignal(id) {
    return VC.focusForensicAnchor(signalDomId(id));
  };
})();
