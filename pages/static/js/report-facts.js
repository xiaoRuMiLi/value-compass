/* 体检页「当期财务数据」面板：核心指标常显 + 三大表关键科目折叠 + 全量科目搜索 + 原文抽取溯源。*/
(function () {
  "use strict";

  const { getJSON } = window.VC;
  const params = new URLSearchParams(location.search);
  const symbol = params.get("symbol") || "600519";
  const host = document.getElementById("reportFacts");
  if (!host) return;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function money(value, unit) {
    if (value == null) return "—";
    if (unit === "元/股" || unit === "倍") return Number(value).toFixed(unit === "倍" ? 3 : 4);
    if (unit === "%") return Number(value).toFixed(2) + "%";
    const n = Math.abs(value);
    if (n >= 1e12) return (value / 1e8).toFixed(2) + " 亿";
    if (n >= 1e8) return (value / 1e8).toFixed(2) + " 亿";
    if (n >= 1e4) return (value / 1e4).toFixed(2) + " 万";
    return Number(value).toFixed(2);
  }

  function yoyNode(value) {
    if (value == null) return el("span", "rf-yoy rf-yoy-na", "—");
    const up = value >= 0;
    return el("span", `rf-yoy ${up ? "rf-yoy-up" : "rf-yoy-down"}`, `${up ? "+" : ""}${value.toFixed(2)}%`);
  }

  function changeText(value, unit) {
    if (value == null) return "—";
    const sign = value > 0 ? "+" : value < 0 ? "−" : "";
    if (unit === "%") return `${sign}${Math.abs(value).toFixed(2)}pt`;
    if (unit === "倍" || unit === "元/股") return `${sign}${Math.abs(value).toFixed(unit === "倍" ? 3 : 4)}`;
    return `${sign}${money(Math.abs(value), unit)}`;
  }

  function changeNode(value, unit) {
    if (value == null) return el("span", "rf-yoy rf-yoy-na", "—");
    const cls = value > 0 ? "rf-yoy-up" : value < 0 ? "rf-yoy-down" : "rf-yoy-na";
    return el("span", `rf-yoy ${cls}`, changeText(value, unit));
  }

  function sourceTag(text) {
    return el("span", "rf-source", text || "东财标准科目");
  }

  function table(items, showKeyOnly) {
    const rows = showKeyOnly ? items.filter((item) => item.key) : items;
    const wrap = el("div", "scroll");
    const tableNode = el("table", "rf-table");
    const thead = el("thead");
    const headRow = el("tr");
    for (const title of ["科目", "本期", "上年同期", "增减额", "同比", "上年全年(参考)", "来源"]) {
      headRow.appendChild(el("th", null, title));
    }
    thead.appendChild(headRow);
    tableNode.appendChild(thead);
    const tbody = el("tbody");
    for (const item of rows) {
      const tr = el("tr");
      const name = el("td", "rf-item-name", item.name || item.code);
      name.title = item.code;
      tr.appendChild(name);
      tr.appendChild(el("td", "rf-num", item.value == null ? "—" : money(item.value, item.unit)));
      tr.appendChild(el("td", "rf-num rf-comparison", item.prior_value == null ? "—" : money(item.prior_value, item.unit)));
      const changeCell = el("td", "rf-num");
      changeCell.appendChild(changeNode(item.change, item.unit));
      tr.appendChild(changeCell);
      const yoyCell = el("td", "rf-num");
      yoyCell.appendChild(yoyNode(item.yoy));
      tr.appendChild(yoyCell);
      tr.appendChild(el("td", "rf-num rf-comparison", item.prior_full_year == null ? "—" : money(item.prior_full_year, item.unit)));
      const src = el("td");
      src.appendChild(sourceTag(item.source));
      tr.appendChild(src);
      tbody.appendChild(tr);
    }
    tableNode.appendChild(tbody);
    wrap.appendChild(tableNode);
    if (!rows.length) wrap.appendChild(el("p", "muted", "本期该表科目未采集"));
    return wrap;
  }

  function readerButton(item, label) {
    const link = el("a", "fx-refresh rf-read-btn", label || "阅");
    link.href = window.VC.readerUrl(item.doc_id, {
      q: item.quote,
      keywords: item.label,
      symbol: symbol,
      from: "report-facts",
    });
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = "跳转财报原文并定位到该字段引文";
    return link;
  }

  function extractedList(items, periodLabel) {
    const wrap = el("div", "rf-extracted");
    if (periodLabel) wrap.appendChild(el("p", "rf-extracted-period", periodLabel));
    if (!items.length) {
      wrap.appendChild(el("p", "muted", "本期没有原文抽取字段"));
      return wrap;
    }
    for (const item of items) {
      const row = el("div", "rf-extracted-row");
      const head = el("div", "rf-extracted-head");
      head.appendChild(el("span", "rf-extracted-label", item.label));
      head.appendChild(el("span", "rf-extracted-value", money(item.value_cny, "元") + " 元"));
      if (item.doc_id) head.appendChild(readerButton(item));
      row.appendChild(head);
      if (item.quote) row.appendChild(el("p", "rf-extracted-quote", "「" + item.quote + "」"));
      const meta = el("p", "rf-extracted-meta", `${item.source_doc || "财报原文"} · ${item.source}`);
      row.appendChild(meta);
      wrap.appendChild(row);
    }
    return wrap;
  }

  function render(data) {
    host.innerHTML = "";
    if (!data || !data.report_date) {
      host.appendChild(el("p", "muted", "库内暂无财务数据，请先运行 fetch_data"));
      return;
    }

    const head = el("div", "rf-head");
    const title = el("h2", null, "当期财务数据");
    const badge = el("span", "rf-period-badge", `${data.report_date} · ${data.report_type}`);
    title.appendChild(badge);
    head.appendChild(title);

    const select = el("select", "rf-period-select");
    const seen = new Set();
    for (const period of data.periods || []) {
      if (seen.has(period.report_date)) continue;
      seen.add(period.report_date);
      const option = el("option", null, `${period.report_date} · ${period.report_type}`);
      option.value = period.report_date;
      if (period.report_date === data.report_date) option.selected = true;
      select.appendChild(option);
    }
    select.addEventListener("change", () => {
      location.href = `/review?symbol=${encodeURIComponent(symbol)}&report_date=${encodeURIComponent(select.value)}`;
    });
    head.appendChild(select);
    host.appendChild(head);
    host.appendChild(el("p", "rf-note", data.note || ""));
    const comparison = data.comparison || {};
    if (comparison.note) {
      host.appendChild(el("p", "rf-compare-note",
        `对比口径：上年同期 ${comparison.prior_period || "—"} · 上年全年 ${comparison.prior_full_year || "—"}。${comparison.note}`));
    }

    const core = el("div", "rf-core");
    for (const metric of data.core || []) {
      const cell = el("div", "rf-core-cell");
      cell.appendChild(el("span", "rf-core-label", metric.label));
      const valueRow = el("div", "rf-core-value-row");
      valueRow.appendChild(el("span", "rf-core-value", money(metric.value, metric.unit)));
      valueRow.appendChild(yoyNode(metric.yoy));
      cell.appendChild(valueRow);
      const compareRow = el("div", "rf-core-compare");
      compareRow.appendChild(el("span", null, "上年同期 " + (metric.prior_value == null ? "—" : money(metric.prior_value, metric.unit))));
      const changeWrap = el("span", null, "增减 ");
      changeWrap.appendChild(changeNode(metric.change, metric.unit));
      compareRow.appendChild(changeWrap);
      cell.appendChild(compareRow);
      cell.appendChild(el("span", "rf-core-source", `${metric.unit} · ${metric.source}`));
      core.appendChild(cell);
    }
    host.appendChild(core);

    const keyDetails = el("details", "fx-details");
    const keySummary = el("summary", null, "三大表关键科目");
    keySummary.appendChild(el("span", "fx-aux-hint", "OCF / 资本开支 / 利息 / 合同负债等 · 同期/全年/增减额对照"));
    keyDetails.appendChild(keySummary);
    for (const stmt of data.statements || []) {
      const box = el("div", "rf-stmt");
      box.appendChild(el("h3", null, stmt.label));
      box.appendChild(table(stmt.items, true));
      keyDetails.appendChild(box);
    }
    host.appendChild(keyDetails);

    const allDetails = el("details", "fx-details");
    const allSummary = el("summary", null, "三大表全部科目");
    allSummary.appendChild(el("span", "fx-aux-hint", "可搜索科目中文名；列含本期/上年同期/增减额/同比/上年全年参考；值为空表示库内未采集/该期未披露"));
    allDetails.appendChild(allSummary);
    const search = el("input", "rf-search");
    search.type = "search";
    search.placeholder = "搜索科目名称或代码，如：合同负债 / CONTRACT_LIAB";
    allDetails.appendChild(search);
    const allHost = el("div", "rf-all");
    for (const stmt of data.statements || []) {
      const box = el("div", "rf-stmt");
      box.appendChild(el("h3", null, stmt.label));
      box.appendChild(table(stmt.items, false));
      allHost.appendChild(box);
    }
    allDetails.appendChild(allHost);
    search.addEventListener("input", () => {
      const needle = search.value.trim().toLowerCase();
      for (const box of allHost.querySelectorAll(".rf-stmt")) {
        let visible = 0;
        for (const tr of box.querySelectorAll("tbody tr")) {
          const hit = !needle || tr.textContent.toLowerCase().includes(needle);
          tr.hidden = !hit;
          if (hit) visible += 1;
        }
        box.hidden = visible === 0;
      }
    });
    host.appendChild(allDetails);

    const extractedDetails = el("details", "fx-details");
    const extractedSummary = el("summary", null, "财报原文抽取字段");
    extractedSummary.appendChild(el("span", "fx-aux-hint", "东财标准科目之外的补充口径，点「阅」回原文核对"));
    extractedDetails.appendChild(extractedSummary);
    extractedDetails.appendChild(extractedList(data.extracted || [], `${data.report_date} 报告期`));
    const latest = data.latest_extracted || {};
    if (latest.report_date && latest.report_date !== data.report_date && (latest.items || []).length) {
      const oldDetails = el("details", "fx-details rf-latest");
      oldDetails.appendChild(el("summary", null, `最近一期抽取字段（${latest.report_date}，与当期不同）`));
      oldDetails.appendChild(extractedList(latest.items, null));
      extractedDetails.appendChild(oldDetails);
    }
    host.appendChild(extractedDetails);
  }

  const url = `/api/stock/${encodeURIComponent(symbol)}/report-facts`
    + (params.get("report_date") ? `?report_date=${encodeURIComponent(params.get("report_date"))}` : "");
  getJSON(url).then(render).catch((err) => {
    host.innerHTML = "";
    host.appendChild(el("p", "muted", "财务数据加载失败：" + err.message));
  });
})();
