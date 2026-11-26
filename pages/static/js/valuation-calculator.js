/* 估值计算器：默认收起，点每个口径的「计算器」按钮弹出，可自定义输入并实时重算。 */
(function () {
  "use strict";

  const METHODS = {
    relative: { name: "相对估值（历史 PE 分位）", fields: ["eps", "peMedian", "peLow", "peHigh", "price"] },
    graham: { name: "格雷厄姆估值", fields: ["eps", "bps", "price"] },
    ddm: { name: "股利折现（Gordon）", fields: ["dps", "growth", "discount", "price"] },
    dcf: { name: "现金流折现（期初经营现金流）", fields: ["fcf", "shares", "growth", "discount", "terminalGrowth", "years", "price"] },
    adjusted_fcf: { name: "自由现金流折现", fields: ["fcfSource", "ocf", "capex", "interestIncome", "interestExpense", "fcf", "shares", "growth", "discount", "terminalGrowth", "years", "price"] },
    fcff: { name: "企业自由现金流折现（FCFF/WACC）", fields: ["ocf", "interestExpense", "capex", "cash", "fairValueAssets", "equityAssets", "debt", "minorityEquity", "revenue", "excessCashRatio", "fairValueDiscount", "equityDiscount", "shares", "growth", "discount", "terminalGrowth", "years", "price"] },
  };

  const FIELD_META = {
    eps: { label: "每股收益 EPS", unit: "元", def: 0 },
    peMedian: { label: "历史 PE 中位数", unit: "倍", def: 0 },
    peLow: { label: "PE 20% 分位", unit: "倍", def: 0 },
    peHigh: { label: "PE 80% 分位", unit: "倍", def: 0 },
    bps: { label: "每股净资产 BPS", unit: "元", def: 0 },
    dps: { label: "每股股利 DPS", unit: "元", def: 0 },
    growth: { label: "增长率 g", unit: "小数，如 0.04", def: 0.04 },
    discount: { label: "折现率 r", unit: "小数，如 0.10", def: 0.10 },
    terminalGrowth: { label: "永续增长率 gt", unit: "小数，如 0.02", def: 0.02 },
    years: { label: "预测年数", unit: "年", def: 10 },
    price: { label: "当前股价", unit: "元", def: 0 },
    shares: { label: "总股本", unit: "股", def: 0 },
    fcf: { label: "期初自由现金流 FCF", unit: "元，直接输入时生效", def: 0 },
    fcfSource: { label: "期初 FCF 来源", unit: "", def: "derived" },
    ocf: { label: "经营活动现金流量净额", unit: "元", def: 0 },
    capex: { label: "资本开支（购建固定资产等）", unit: "元，未取到请手工填入，勿按 0", def: 0 },
    interestIncome: { label: "利息收入", unit: "元，未取到请手工填入，勿按 0", def: 0 },
    interestExpense: { label: "利息支出", unit: "元，未取到请手工填入，勿按 0", def: 0 },
    inventory: { label: "存货增加额（提示项）", unit: "元，不进入公式", def: 0 },
    receivable: { label: "应收增加额（提示项）", unit: "元，不进入公式", def: 0 },
    payable: { label: "应付增加额（提示项）", unit: "元，不进入公式", def: 0 },
    prepaid: { label: "预付款项增加额（提示项，压低OCF）", unit: "元，不进入公式", def: 0 },
    contractLiability: { label: "合同负债/经销商预付款增加额（提示项，抬高OCF）", unit: "元，不进入公式", def: 0 },
    cash: { label: "货币资金及存款理财", unit: "元，现金+短期/中长期存款理财，按面值加回、不打折；受限制资金请自行剔除", def: 0 },
    fairValueAssets: { label: "公允价值类金融资产", unit: "元，交易性/指定公允价值金融资产等，按公允价值类折价计入", def: 0 },
    equityAssets: { label: "权益类投资", unit: "元，联营/合营/权益工具/投资物业等，按权益类折价计入", def: 0 },
    marketableSecurities: { label: "交易性金融资产/短期存款（旧口径）", unit: "元，兼容旧数据，优先用上方公允价值类", def: 0 },
    nonoperatingInvest: { label: "非经营投资原值（旧口径）", unit: "元，兼容旧数据，优先用上方权益类", def: 0 },
    debt: { label: "有息负债", unit: "元，短期+长期借款、债券、租赁负债、一年内到期部分", def: 0 },
    minorityEquity: { label: "少数股东权益", unit: "元，桥接时扣减", def: 0 },
    revenue: { label: "营业收入（最近完整财年）", unit: "元，用于计提最低经营现金", def: 0 },
    excessCashRatio: { label: "最低经营现金比例", unit: "小数，营收 × 该比例", def: 0.02 },
    fairValueDiscount: { label: "公允价值类折价系数", unit: "小数，如 0.8 表示打八折", def: 0.8 },
    equityDiscount: { label: "权益类投资折价系数", unit: "小数，如 0.8 表示打八折", def: 0.8 },
    nonoperatingDiscount: { label: "非经营投资折价系数（旧口径）", unit: "兼容旧数据，优先用上方两类系数", def: 0.8 },
  };

  const SOURCES = [
    { value: "derived", label: "标准公式：OCF − 资本开支 + 利息收入 − 利息支出" },
    { value: "direct", label: "直接输入 FCF（港股财报原文 / 已计算值）" },
  ];

  let modal = null;
  let current = null;
  let defaults = {};

  function ensureModal() {
    if (modal) return modal;
    modal = document.createElement("div");
    modal.className = "calc-modal";
    modal.innerHTML =
      '<div class="calc-backdrop" data-calc-close></div>' +
      '<div class="calc-dialog" role="dialog" aria-modal="true">' +
      '<div class="calc-header"><h3 id="calcTitle"></h3>' +
      '<button class="calc-close" type="button" aria-label="关闭" data-calc-close>×</button></div>' +
      '<p class="calc-hint">数值已回填页面当前所用值；可自由修改。金额单位见各字段标注，计算器在浏览器本地运行，不上传数据。</p>' +
      '<div class="calc-form" id="calcForm"></div>' +
      "</div>";
    document.body.appendChild(modal);
    modal.querySelectorAll("[data-calc-close]").forEach((n) =>
      n.addEventListener("click", close)
    );
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal.classList.contains("open")) close();
    });
    return modal;
  }

  function fmt(v, digits) {
    if (v == null || !isFinite(v)) return "-";
    return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: digits == null ? 4 : digits });
  }

  function num(id) {
    const el = document.getElementById(id);
    if (!el) return 0;
    const v = parseFloat(el.value);
    return isNaN(v) ? 0 : v;
  }

  function val(id) {
    const el = document.getElementById(id);
    return el ? el.value : "";
  }

  function addField(form, key, value) {
    const meta = FIELD_META[key];
    if (!meta) return;
    const wrap = document.createElement("div");
    wrap.className = "calc-field";
    const label = document.createElement("label");
    label.textContent = meta.label;
    if (meta.unit) {
      const u = document.createElement("span");
      u.className = "unit";
      u.textContent = "（" + meta.unit + "）";
      label.appendChild(u);
    }
    wrap.appendChild(label);
    let input;
    if (key === "fcfSource") {
      input = document.createElement("select");
      for (const s of SOURCES) {
        const opt = document.createElement("option");
        opt.value = s.value;
        opt.textContent = s.label;
        if (s.value === value) opt.selected = true;
        input.appendChild(opt);
      }
    } else {
      input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.value = value == null ? meta.def : value;
    }
    input.id = "calc-" + key;
    input.addEventListener("input", compute);
    input.addEventListener("change", compute);
    wrap.appendChild(input);
    form.appendChild(wrap);
  }

  function renderForm() {
    const form = document.getElementById("calcForm");
    form.innerHTML = "";
    const fields = METHODS[current].fields;
    if (current === "fcff") {
      const flow = document.createElement("div");
      flow.className = "calc-group";
      const flowTitle = document.createElement("div");
      flowTitle.className = "calc-group-title";
      flowTitle.textContent = "企业自由现金流（OCF + 实付利息 − 资本开支）";
      flow.appendChild(flowTitle);
      form.appendChild(flow);
      for (const k of ["ocf", "interestExpense", "capex"]) addField(form, k, defaults[k]);
      const bridge = document.createElement("div");
      bridge.className = "calc-group";
      const bridgeTitle = document.createElement("div");
      bridgeTitle.className = "calc-group-title";
      bridgeTitle.textContent = "EV → 股权价值桥接（减有息负债，加面值现金存款 + 折价后公允价值/权益类资产）";
      bridge.appendChild(bridgeTitle);
      form.appendChild(bridge);
      for (const k of ["cash", "fairValueAssets", "equityAssets", "debt",
                       "minorityEquity", "revenue", "excessCashRatio",
                       "fairValueDiscount", "equityDiscount"]) {
        addField(form, k, defaults[k]);
      }
      for (const k of ["shares", "growth", "discount", "terminalGrowth", "years", "price"]) {
        addField(form, k, defaults[k]);
      }
    } else if (current === "adjusted_fcf") {
      const group = document.createElement("div");
      group.className = "calc-group";
      const groupTitle = document.createElement("div");
      groupTitle.className = "calc-group-title";
      groupTitle.textContent = "期初自由现金流（标准口径：OCF − 资本开支 + 利息收入 − 利息支出）";
      group.appendChild(groupTitle);
      form.appendChild(group);
      addField(form, "fcfSource", defaults.fcfSource || "derived");
      for (const k of ["ocf", "capex", "interestIncome", "interestExpense", "fcf"]) {
        addField(form, k, defaults[k]);
      }
      const wc = document.createElement("div");
      wc.className = "calc-group";
      const wcTitle = document.createElement("div");
      wcTitle.className = "calc-group-title";
      wcTitle.textContent = "营运资本差额（仅作现金流质量提示，不进入计算公式）";
      wc.appendChild(wcTitle);
      form.appendChild(wc);
      for (const k of ["inventory", "receivable", "payable", "prepaid", "contractLiability"]) addField(form, k, defaults[k]);
      for (const k of ["shares", "growth", "discount", "terminalGrowth", "years", "price"]) {
        addField(form, k, defaults[k]);
      }
    } else {
      for (const k of fields) addField(form, k, defaults[k]);
    }

    const actions = document.createElement("div");
    actions.className = "calc-actions";
    const run = document.createElement("button");
    run.type = "button";
    run.className = "calc-run";
    run.textContent = "计算结果";
    run.addEventListener("click", compute);
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "calc-reset";
    reset.textContent = "恢复页面值";
    reset.addEventListener("click", () => open(current, defaultsRaw));
    actions.appendChild(run);
    actions.appendChild(reset);
    form.appendChild(actions);

    const note = document.createElement("div");
    note.className = "calc-formula-note";
    note.textContent = FORMULA_NOTES[current] || "";
    form.appendChild(note);

    const result = document.createElement("div");
    result.className = "calc-result";
    result.id = "calcResult";
    form.appendChild(result);
  }

  const FORMULA_NOTES = {
    relative: "合理价 = 历史 PE 中位数 × EPS；区间 = 20%/80% 分位 PE × EPS。",
    graham: "内在价值 = √(22.5 × EPS × BPS)；区间取 ±20%。",
    ddm: "合理价 = DPS × (1+g) ÷ (r−g)；区间取 ±20%。要求 r > g。",
    dcf: "前 N 年 FCF 按 g 增长逐年以 r 折现，加永续终值折现，除以股本；区间 ±20%。",
    adjusted_fcf: "期初FCF = 经营活动现金流量净额 − 资本开支 + 利息收入 − 利息支出；其后与两阶段 DCF 相同。存货/应收/应付/预付差额只作提示，不进入公式；缺失的资本开支与利息项按 0 处理，请按财报补填。",
    fcff: "FCFF = 经营现金流 + 实付利息 − 资本开支；按 WACC（discount）两阶段折现得 EV；股权价值 = EV − 有息负债 + 超额现金 + 公允价值类资产×折价 + 权益类投资×折价 − 少数股东权益。现金与短期/中长期存款理财按面值加回、不打折；超额现金 = 现金存款 − 营收×最低经营现金比例。区间 ±20%。页面校准值以市场无风险利率为下限（A股中国10Y国债/其它美国10Y国债），此处为手动试算，可直接调整 WACC 与各档折价观察敏感性。",
  };

  function resultBlock(title, rows, invalid) {
    const box = document.getElementById("calcResult");
    box.className = "calc-result" + (invalid ? " invalid" : "");
    box.innerHTML = "";
    const h = document.createElement("div");
    h.className = "calc-result-title";
    h.textContent = title;
    box.appendChild(h);
    for (const [k, v] of rows) {
      const row = document.createElement("div");
      row.className = "calc-result-row";
      const a = document.createElement("span");
      a.textContent = k;
      const b = document.createElement("span");
      b.className = "calc-rv";
      b.textContent = v;
      row.appendChild(a);
      row.appendChild(b);
      box.appendChild(row);
    }
  }

  function compute() {
    let low, high, center, margin, detail = [];
    const p = num("calc-price");
    if (current === "relative") {
      const eps = num("calc-eps"), pm = num("calc-peMedian");
      const pl = num("calc-peLow") || pm * 0.8, ph = num("calc-peHigh") || pm * 1.2;
      center = pm * eps; low = pl * eps; high = ph * eps;
    } else if (current === "graham") {
      const eps = num("calc-eps"), bps = num("calc-bps");
      center = Math.sqrt(22.5 * eps * bps); low = center * 0.8; high = center * 1.2;
    } else if (current === "ddm") {
      const dps = num("calc-dps"), g = num("calc-growth"), r = num("calc-discount");
      if (r <= g) {
        resultBlock("参数无效：折现率 r 必须大于增长率 g", [], true);
        return;
      }
      center = dps * (1 + g) / (r - g); low = center * 0.8; high = center * 1.2;
    } else if (current === "fcff") {
      const g = num("calc-growth"), r = num("calc-discount");
      const gt = num("calc-terminalGrowth"), years = Math.max(1, Math.round(num("calc-years") || 10));
      const shares = num("calc-shares");
      const ocf = num("calc-ocf"), interest = num("calc-interestExpense"), capex = num("calc-capex");
      const base = ocf + interest - capex;
      if (r <= gt || !shares || !base) {
        resultBlock("参数无效：需 WACC r > 永续增长率 gt，且 FCFF、股本大于 0", [], true);
        return;
      }
      let cashFlow = base, pv = 0;
      for (let y = 1; y <= years; y++) {
        cashFlow *= 1 + g;
        pv += cashFlow / Math.pow(1 + r, y);
      }
      const terminalPv = cashFlow * (1 + gt) / (r - gt) / Math.pow(1 + r, years);
      const ev = pv + terminalPv;
      const ratio = Math.max(0, num("calc-excessCashRatio"));
      const fvDisc = Math.max(0, Math.min(1, num("calc-fairValueDiscount")));
      const eqDisc = Math.max(0, Math.min(1, num("calc-equityDiscount")));
      const minCash = num("calc-revenue") * ratio;
      // 现金及存款理财按面值加回、不打折；公允价值类与权益类分别按各自系数折价。
      const excessCash = Math.max(0, num("calc-cash") - minCash);
      const fvAfter = num("calc-fairValueAssets") * fvDisc;
      const eqAfter = num("calc-equityAssets") * eqDisc;
      const equity = ev - num("calc-debt") + excessCash + fvAfter + eqAfter - num("calc-minorityEquity");
      if (equity <= 0) {
        resultBlock("桥接后股权价值 ≤ 0：请检查有息负债与两类投资资产取值", [], true);
        return;
      }
      center = equity / shares; low = center * 0.8; high = center * 1.2;
      detail.push(["企业自由现金流 FCFF", fmt(base) + " 元"]);
      detail.push(["企业价值 EV（两阶段折现）", fmt(ev) + " 元"]);
      detail.push(["最低经营现金 / 超额现金", fmt(minCash) + " / " + fmt(excessCash) + " 元"]);
      detail.push(["公允价值类资产（折价后）", fmt(fvAfter) + " 元"]);
      detail.push(["权益类投资（折价后）", fmt(eqAfter) + " 元"]);
      detail.push(["股权价值（桥接后）", fmt(equity) + " 元"]);
    } else {
      // dcf / adjusted_fcf 共用两阶段
      const g = num("calc-growth"), r = num("calc-discount");
      const gt = num("calc-terminalGrowth"), years = Math.max(1, Math.round(num("calc-years") || 10));
      const shares = num("calc-shares");
      let base = num("calc-fcf");
      if (current === "adjusted_fcf") {
        const src = val("calc-fcfSource");
        if (src === "direct") {
          base = num("calc-fcf");
          detail.push(["期初FCF来源", "直接输入"]);
        } else {
          const ocf = num("calc-ocf"), capex = num("calc-capex");
          const inc = num("calc-interestIncome"), exp = num("calc-interestExpense");
          base = ocf - capex + inc - exp;
          detail.push(["经营活动现金流量净额", fmt(ocf) + " 元"]);
          detail.push(["资本开支", "− " + fmt(capex) + " 元"]);
          detail.push(["利息收入 / 支出", "+ " + fmt(inc) + " / − " + fmt(exp) + " 元"]);
        }
        detail.push(["期初自由现金流", fmt(base) + " 元"]);
        if (defaults.workingCapitalAlert) {
          detail.push(["现金流质量提示", defaults.workingCapitalAlert]);
        }
      }
      if (r <= gt || !shares || !base) {
        resultBlock("参数无效：需折现率 r > 永续增长率 gt，且现金流、股本大于 0", [], true);
        return;
      }
      let cash = base, pv = 0;
      for (let y = 1; y <= years; y++) {
        cash *= 1 + g;
        pv += cash / Math.pow(1 + r, y);
      }
      const terminal = cash * (1 + gt) / (r - gt);
      const terminalPv = terminal / Math.pow(1 + r, years);
      center = (pv + terminalPv) / shares;
      low = center * 0.8; high = center * 1.2;
      detail.push(["现值合计", fmt(pv + terminalPv) + " 元"]);
    }
    margin = p > 0 ? center / p - 1 : null;
    const rows = [
      ["合理价中枢", fmt(center, 2) + " 元"],
      ["合理价区间", fmt(low, 2) + " ~ " + fmt(high, 2) + " 元"],
      ["当前股价", fmt(num("calc-price"), 2) + " 元"],
      ["安全边际", margin == null ? "-" : (margin * 100).toFixed(2) + "%"],
    ].concat(detail);
    resultBlock("计算结果", rows, false);
  }

  let defaultsRaw = {};

  function open(method, data) {
    current = method;
    defaultsRaw = data || {};
    defaults = Object.assign({}, data || {});
    ensureModal();
    document.getElementById("calcTitle").textContent = METHODS[method].name + " · 自定义计算器";
    renderForm();
    compute();
    modal.classList.add("open");
  }

  function close() {
    if (modal) modal.classList.remove("open");
  }

  window.ValuationCalculator = { open: open, close: close };
})();
