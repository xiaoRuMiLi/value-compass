"""财报关键点 → 日 K 线事件带：前端静态接线与事件点构建回归测试。

前端是原生 JS（IIFE + window.VC），逻辑放在 ``forensics.js`` 的纯函数里，
这里用 Node 的 vm 加载真实脚本后断言，避免把实现复制一份到测试里造成漂移。
"""

from __future__ import annotations

import shutil
import re
import subprocess
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_FORENSICS_JS = _ROOT / "pages" / "static" / "js" / "forensics.js"
_REPORT_JS = _ROOT / "pages" / "static" / "js" / "report.js"
_REPORT_HTML = _ROOT / "pages" / "report.html"
_REVIEW_HTML = _ROOT / "pages" / "review.html"
_REPORT_CSS = _ROOT / "pages" / "static" / "css" / "report.css"

# 与线上同一份脚本，用假 K 线与假体检报告验证：色调分级、报告期锚定、区间取末次、
# 高严重度兜底、meta 详情、空数据降级。
_NODE_CHECK = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const ctx = { window: { VC: {} }, console: console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const VC = ctx.window.VC;

const AMP = '&' + 'amp;', LT = '&' + 'lt;', GT = '&' + 'gt;', QUOT = '&' + 'quot;';
const escaped = LT + 'a href=' + QUOT + 'x' + QUOT + GT + AMP;
assert.strictEqual(VC.escapeHtml('<a href="x">&'), escaped);

const kline = [
  { trade_date: '2023-12-29' }, { trade_date: '2024-01-02' },
  { trade_date: '2024-12-31' }, { trade_date: '2025-01-02' },
  { trade_date: '2025-04-01' },
];
const data = {
  scan: { signals: [
    { id: 'S1', title: 'ROE change', category: 'return', severity: 'high',
      period: '2023-12-31 FY ~ 2024-12-31 FY (2 periods)', description: 'd1' },
    { id: 'S2', title: 'price divergence', category: 'market', severity: 'medium',
      period: '2024-12-31', description: 'd2' },
    { id: 'S3', title: 'margin move', category: 'margin', severity: 'low',
      period: '2024-06-30 HY', description: 'd3' },
  ] },
  analyses: [
    { id: 'S1', judgement: 'normal', substance: 's1', benign: 'b1' },
    { id: 'S2', judgement: 'suspicious', substance: 's2', risk: 'r2' },
    { id: 'S3', judgement: 'unverifiable' },
  ],
};
const r = VC.buildForensicMarkers(data, kline);
assert.strictEqual(r.points.length, 3);
assert.strictEqual(r.counts.positive, 1);
assert.strictEqual(r.counts.negative, 1);
assert.strictEqual(r.counts.neutral, 1);
// normal -> positive / red (A-share convention: good news is red)
assert.strictEqual(r.points[0].tone, 'positive');
assert.strictEqual(r.points[0].itemStyle.color, '#dc2626');
// period range uses the LAST report date -> exact trading day 2024-12-31
assert.strictEqual(r.points[0].value[0], '2024-12-31');
// suspicious -> negative / green
assert.strictEqual(r.points[1].tone, 'negative');
assert.strictEqual(r.points[1].itemStyle.color, '#16a34a');
// unverifiable -> neutral; non-trading 2024-06-30 anchors to next trading day
assert.strictEqual(r.points[2].tone, 'neutral');
assert.strictEqual(r.points[2].value[0], '2024-12-31');
assert.strictEqual(r.points[2].itemStyle.color, '#64748b');
// meta carries everything the tooltip / click handler needs
assert.strictEqual(r.points[0].signalId, 'S1');
assert.ok(r.points[0].meta.detail.indexOf('s1') >= 0);
assert.ok(r.points[0].meta.toneLabel.length > 0);
assert.strictEqual(r.earliest, '2024-12-31');

// no analysis + high severity still counts as a warning (green)
const r2 = VC.buildForensicMarkers(
  { scan: { signals: [{ id: 'S9', title: 'x', severity: 'high', period: '2025-01-01' }] }, analyses: [] },
  kline
);
assert.strictEqual(r2.points[0].tone, 'negative');
assert.strictEqual(r2.points[0].value[0], '2025-01-02');

// same trading day + same tone must be offset so points do not fully overlap
const r3 = VC.buildForensicMarkers(
  { scan: { signals: [
    { id: 'A', title: 'a', severity: 'medium', period: '2024-12-31' },
    { id: 'B', title: 'b', severity: 'medium', period: '2024-12-31' },
  ] }, analyses: [{ id: 'A', judgement: 'normal' }, { id: 'B', judgement: 'normal' }] },
  kline
);
assert.strictEqual(r3.points.length, 2);
assert.ok(r3.points[1].value[1] > r3.points[0].value[1]);

// ---- 原文通读发现也进入事件带：单独一类、菱形、带原文摘录与反向定位锚点 ----
const mining = {
  new_findings: [
    { description: '处置收益大幅上升', quote: '出售若干投資之收益約 128 億元',
      period: '2024', report_label: '2024 年报', is_current: false },
    { description: '分占联营盈利回升', quote: '應佔聯營公司盈利 251.76 億元',
      period: '2025', report_label: '2025 一季报' },
    { description: '没有报告期的线索', quote: 'x', period: '', report_label: '' },
  ],
};
const rm = VC.buildForensicMarkers(Object.assign({}, data, { report_mining: mining }), kline);
assert.strictEqual(rm.points.length, 5);          // 3 个规则信号 + 2 条通读线索
assert.strictEqual(rm.counts.clue, 2);
const c0 = rm.points[3], c1 = rm.points[4];
assert.strictEqual(c0.tone, 'clue');
assert.strictEqual(c0.itemStyle.color, '#d97706');
assert.strictEqual(c0.symbol, 'diamond');
assert.strictEqual(c0.signalId, null);
assert.strictEqual(c0.anchorId, 'fx-finding-1');
// 年份 + 报告期标签还原报告期末：年报 -> 12-31，一季报 -> 03-31 后顺延到最近交易日
assert.strictEqual(c0.value[0], '2024-12-31');
assert.strictEqual(c1.value[0], '2025-04-01');
assert.strictEqual(c1.anchorId, 'fx-finding-2');
assert.strictEqual(c0.meta.period, '2024 年报');
assert.strictEqual(c0.meta.judgement, '规则扫描未覆盖');
assert.ok(c0.meta.detail.indexOf('128') >= 0);    // 悬停能看到财报原文摘录
assert.ok(c0.meta.hint.indexOf('原文通读发现') >= 0);
assert.strictEqual(VC.MARKER_TONES.clue.label, '原文通读线索');
// 完整日期优先于年份推断；没有年份的线索直接跳过，不落在错误的时点
const explicit = VC.buildForensicMarkers(
  { report_mining: { new_findings: [
    { description: 'd', quote: 'q', period: '2023-12-31', report_label: '2023 年报' },
  ] } }, kline
);
assert.strictEqual(explicit.points.length, 1);
assert.strictEqual(explicit.points[0].value[0], '2024-01-02');
// 只有通读线索时，视窗拉近的 earliest 也要跟着更新
const onlyClue = VC.buildForensicMarkers(
  { report_mining: { new_findings: [{ description: 'd', period: '2023', report_label: '2023 年报' }] } },
  kline
);
assert.strictEqual(onlyClue.earliest, '2024-01-02');

// 点击线索点 -> 反向定位到上方通读发现卡片（含展开折叠的祖先 details）
const nodes = {};
ctx.document = { getElementById: (id) => nodes[id] || null };
ctx.window.setTimeout = () => 0;
const parentDetails = {
  tagName: 'DETAILS', parentElement: null,
  setAttribute: (k, v) => { parentDetails.opened = k + '=' + v; },
};
const findingNode = {
  tagName: 'DIV', parentElement: parentDetails, setAttribute: () => {},
  scrollIntoView: function () { this.scrolled = true; },
  classList: { add: function (c) { this.added = c; }, remove: function (c) { this.removed = c; } },
};
nodes['fx-finding-1'] = findingNode;
assert.strictEqual(VC.focusForensicAnchor('fx-finding-1'), true);
assert.strictEqual(findingNode.scrolled, true);
assert.strictEqual(findingNode.classList.added, 'fx-signal-flash');
assert.strictEqual(parentDetails.opened, 'open=open');
assert.strictEqual(VC.focusForensicAnchor('fx-finding-nope'), false);
// 旧接口仍按信号 id 委托到同一个定位逻辑
const signalNode = Object.assign({}, findingNode, {
  tagName: 'DETAILS', parentElement: null,
  scrollIntoView: function () { this.scrolled = true; },
  classList: { add: () => {}, remove: () => {} },
});
nodes['fx-signal-S1'] = signalNode;
assert.strictEqual(VC.focusForensicSignal('S1'), true);
assert.strictEqual(signalNode.scrolled, true);

// missing data degrades to an empty event band, never throws
assert.strictEqual(VC.buildForensicMarkers(null, kline).points.length, 0);
assert.strictEqual(VC.buildForensicMarkers(data, []).points.length, 0);
assert.strictEqual(VC.buildForensicMarkers(data, kline).counts.clue, 0);

// ---- 判定依据不再吐原始 JSON：中文标签、单位换算、表格与去重 ----
assert.strictEqual(VC.formatFactValue('ocf', 1315866293.65), '13.16 亿元');
assert.strictEqual(VC.formatFactValue('top5_pct', 75.98), '75.98%');
assert.strictEqual(VC.formatFactValue('gap_pp', 12.3), '12.30 个百分点');
assert.strictEqual(VC.formatFactValue('asset_expansion_confirmed', false), '否');
assert.strictEqual(VC.formatFactValue('entity_count', 9), '9 家');
assert.strictEqual(VC.formatFactValue('ratio', 0.351), '0.351');
function mockEl(tag) {
  return { tagName: String(tag).toUpperCase(), className: '', textContent: '', children: [],
    appendChild: function (c) { this.children.push(c); return c; }, setAttribute: function () {} };
}
ctx.document = { getElementById: (id) => nodes[id] || null, createElement: mockEl };
const factNode = VC.renderPatternFacts({
  series: [{ period: '2026-09-30', ocf: 8008444194.49, net_profit: 22237939006.75, ratio: 0.36 }],
  low_periods: 6, consecutive_low: 3, asset_expansion_confirmed: false,
  occurrences: [{ period: '2025', top5_pct: 75.98, related_sales_pct: 8.1,
                  quote: '前五名客户合计销售金额占年度销售总额比例 75.98%', source: '2025 年报' }],
}, new Set());
function flatten(n) {
  let s = n.textContent || '';
  for (const c of n.children || []) s += ' ' + flatten(c);
  return s;
}
const factText = flatten(factNode);
assert.ok(factText.indexOf('经营活动现金流净额') >= 0, factText);
assert.ok(factText.indexOf('80.08 亿元') >= 0, factText);
assert.ok(factText.indexOf('前五名客户占比') >= 0, factText);
assert.ok(factText.indexOf('75.98%') >= 0, factText);
assert.ok(factText.indexOf('连续偏低期数') >= 0, factText);
assert.ok(factText.indexOf('否') >= 0, factText);
assert.ok(factText.indexOf('2025 年报') >= 0, factText);
assert.strictEqual(factText.indexOf('{'), -1, '不应再出现 JSON 花括号');
assert.strictEqual(factText.indexOf('"ocf"'), -1, '不应再出现英文键名');
// 卡片正文已展示过的原文摘录，展开区不重复
const dedup = VC.renderPatternFacts(
  { occurrences: [{ period: '2025', quote: '同一段原文', top5_pct: 75.98 }] },
  new Set(['同一段原文'])
);
assert.strictEqual(flatten(dedup).indexOf('同一段原文'), -1);
// 空 facts 优雅降级，不抛异常
assert.ok(flatten(VC.renderPatternFacts({}, new Set())).indexOf('已展示在卡片正文中') >= 0);
// 阈值里的嵌套子表（fraud_patterns）必须逐行展开，不能渲染成 [object Object]
const thNode = VC.renderThresholds({
  thresholds_fingerprint: 'abc123',
  thresholds: {
    lookback_periods: 12,
    rate_require_outlier: ['debt_ratio'],
    fraud_patterns: { cash_conversion_low: 0.7, ar_divergence_pp: 30.0 },
  },
});
const thText = flatten(thNode);
assert.ok(thText.indexOf('fraud_patterns.cash_conversion_low') >= 0, thText);
assert.ok(thText.indexOf('0.7') >= 0, thText);
assert.strictEqual(thText.indexOf('[object Object]'), -1, thText);
// ---- 季度还原 / 全年 vs 累计现金含量对照：金额换算、缺失降级、只并列不相减 ----
const qdata = {
  method: '同一自然年内累计值差分',
  annual_cash_conversion: { period: '2025-12-31', report_type: 'FY',
    ocf: 303052000000, net_profit: 224842000000, ratio: 1.348 },
  latest_cumulative_cash_conversion: { period: '2026-09-30', report_type: 'Q3',
    ocf: 156713000000, net_profit: 177727000000, ratio: 0.882 },
  quarters: [
    { label: '2026 Q1', net_profit: 58090000000, ocf: null, revenue: null },
    { label: '2026 Q2', net_profit: 56030000000, ocf: 52710000000, revenue: 180000000000 },
  ],
};
const qv = VC.buildQuarterlyView(qdata);
assert.strictEqual(qv.annual.ratio, '1.348');
assert.strictEqual(qv.annual.ocf, '3030.52 亿元');
assert.strictEqual(qv.annual.netProfit, '2248.42 亿元');
assert.strictEqual(qv.latest.period, '2026-09-30');
assert.strictEqual(qv.latest.ratio, '0.882');
assert.strictEqual(qv.quarters.length, 2);
assert.strictEqual(qv.quarters[0].ocf, '—', '缺失科目必须留空，不能补 0');
assert.strictEqual(qv.quarters[1].netProfit, '560.30 亿元');
assert.strictEqual(VC.buildQuarterlyView(null).quarters.length, 0);
assert.strictEqual(VC.buildQuarterlyView({}).annual, null);
const manyQ = { quarters: [] };
for (let i = 1; i <= 10; i++) manyQ.quarters.push({ label: 'Q' + i, net_profit: i, ocf: i, revenue: i });
assert.strictEqual(VC.buildQuarterlyView(manyQ).quarters.length, 8, '只展示最近 8 期');
assert.strictEqual(VC.buildQuarterlyView(manyQ).quarters[0].label, 'Q3');
const qNode = VC.renderQuarterly({ scan: { quarterly: qdata } });
const qText = flatten(qNode);
assert.ok(qText.indexOf('最近完整年度') >= 0, qText);
assert.ok(qText.indexOf('最新累计期') >= 0, qText);
assert.ok(qText.indexOf('3030.52 亿元') >= 0, qText);
assert.ok(qText.indexOf('只能并列、不能相减') >= 0, qText);
assert.ok(qText.indexOf('展开单季还原明细') >= 0, qText);
assert.strictEqual(qText.indexOf('[object Object]'), -1, qText);
assert.strictEqual(VC.renderQuarterly({ scan: {} }), null, '旧缓存无 quarterly 时应安静降级');
// ---- 因子注册表清单：后端单一来源下发，页面不维护第二份名单 ----
const specData = {
  fraud_patterns: {
    patterns: [], summary: { pattern_count: 0, high: 0, medium: 0, low: 0 },
    coverage: {
      performed: [], skipped: [], checks_total: 15,
      by_evidence_kind: { quantitative: 11, documentary: 4, hybrid: 0 },
      factor_specs: [
        { id: 'FRD012', check_name: '关联方资金占用（原文取证）', evidence_kind: 'documentary', red_flag_refs: ['RF-GV-01'] },
        { id: 'FRD001', check_name: '应收账款增速背离', evidence_kind: 'quantitative', red_flag_refs: ['RF-AQ-04'] },
      ],
      documentary_rejected_quotes: 2,
    },
  },
};
const specNode = VC.renderFraudPatterns(specData);
const specText = flatten(specNode);
assert.ok(specText.indexOf('展开因子注册表清单（2 项') >= 0, specText);
assert.ok(specText.indexOf('FRD012') >= 0, specText);
assert.ok(specText.indexOf('原文取证') >= 0, specText);
assert.ok(specText.indexOf('RF-GV-01') >= 0, specText);
assert.ok(specText.indexOf('数值型 11 项') >= 0, specText);
assert.ok(specText.indexOf('已剔除且不计入判定') >= 0, specText);
assert.strictEqual(specText.indexOf('[object Object]'), -1, specText);
// 旧缓存没有 factor_specs 时安静降级，不抛异常
assert.ok(VC.renderFraudPatterns({ fraud_patterns: { patterns: [], coverage: {}, summary: {} } }));
console.log('MARKER_LOGIC_OK');
"""

# 审计视角板块用极简 DOM mock 真实渲染一次：验证告警、关键事实、反证去重都在页面上，
# 且没有 audit_evidence 时安静降级（旧缓存报告仍能正常打开）。
_NODE_AUDIT_CHECK = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
function makeNode(tag) {
  return {
    tagName: tag, className: '', textContent: '', children: [], id: '', attrs: {},
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
}
const ctx = { window: { VC: {} }, console: console,
  document: { createElement: makeNode, getElementById: () => null },
  setTimeout: () => {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const VC = ctx.window.VC;
const data = { audit_evidence: {
  source_docs: [{ period: '2025', report_label: '2025 年报' }],
  alerts: [
    { level: 'high', title: '口径差异', detail: '营收降幅含口径调整', quote: '原文1', source: '2025 年报' },
    { level: 'medium', title: '关联存款', detail: '大额资金存放', quote: '原文2', source: '2025 年报' },
  ],
  audit_opinion: [{ period: '2025', source: '2025 年报', opinion: '标准的无保留意见', firm: '天职国际', quote: 'q' }],
  key_audit_matters: [{ period: '2025', source: '2025 年报', items: [
    { title: '货币资金的存在性和完整性' }, { title: '营业收入的确认' }], quote: 'q' }],
  quarterly_discrepancy: [{ period: '2025', source: '2025 年报', applicable: true, reason: '调整收入确认', quote: 'q' }],
  accounting_change: [{ period: '2025', source: '2025 年报', applicable: false, quote: 'q' }],
  finance_company_deposit: [{ period: '2025', source: '2025 年报', balance_yi: 479.77,
    share_of_cash_pct: 37.77, limit_yi: 550, quote: 'q' }],
  dividend_coverage: [{ period: '2025', source: '2025 年报', cash_dividend_yi: 223.08,
    net_profit_yi: 89.54, payout_ratio_pct: 249.13, quote: 'q' }],
  internal_control: [{ period: '2025', source: '2025 年报', opinion: '标准无保留意见', major_defects: 0, quote: 'q' }],
  operating_volume: [{ period: '2025', source: '2025 年报', capacity_utilization_pct: 63.4,
    in_build_capacity_wan_ton: 3.95, quote: 'q' }],
  counter_evidence: [
    { period: '2025', item: '诚信状况良好', source: '2025 年报', quote: '公司及控股股东诚信状况良好。' },
    { period: '2025', item: '诚信状况良好', source: '2025 年报', quote: '重复项应被去重' },
    { period: '2024', item: '诚信状况良好', source: '2024 年报', quote: '旧年份不应展示' },
    { period: '2025', item: '内控有效', source: '2025 年报', quote: '在所有重大方面保持了有效的财务报告内部控制。' },
  ],
}};
const node = VC.renderAuditEvidence(data);
assert.ok(node, 'audit_evidence 存在时应渲染板块');
function walk(n, out) { out.push(n); (n.children || []).forEach((c) => walk(c, out)); return out; }
const texts = walk(node, []).map((n) => String(n.textContent || ''));
assert.ok(texts.some((t) => t.indexOf('审计视角') >= 0), '缺少板块标题');
assert.ok(texts.some((t) => t.indexOf('口径差异') >= 0), '缺少 high 告警');
assert.ok(texts.some((t) => t.indexOf('关联存款') >= 0), '缺少 medium 告警');
assert.ok(texts.some((t) => t.indexOf('479.77') >= 0), '缺少关联存款事实');
assert.ok(texts.some((t) => t.indexOf('货币资金的存在性和完整性') >= 0), '缺少关键审计事项');
assert.ok(texts.some((t) => t.indexOf('调整收入确认') >= 0), '缺少口径差异事实');
assert.strictEqual(texts.filter((t) => t === '诚信状况良好').length, 1, '反证未按最新期去重');
assert.strictEqual(texts.filter((t) => t.indexOf('旧年份') >= 0).length, 0, '不应展示旧年份反证');
assert.strictEqual(VC.renderAuditEvidence({}), null, '无 audit_evidence 时应安静降级');
console.log('AUDIT_RENDER_OK');
"""


class StaticWiringTests(unittest.TestCase):
    def test_report_page_has_event_band_container(self):
        html = _REPORT_HTML.read_text(encoding="utf-8")
        self.assertIn('id="klineMarkerLegend"', html)
        self.assertIn("财报关键点事件带", html)
        # 静态资源版本会随功能升级而变化，这里只校验「带了版本号且各资源一致」，
        # 避免每次升版本都要改测试（写死版本的断言会让升级被误判为回归）。
        versions = re.findall(r"/static/(?:js|css)/[\w.-]+\?v=([\w]+)", html)
        self.assertTrue(versions, "report.html 未带静态资源版本号")
        self.assertEqual(len(set(versions)), 1, f"report.html 静态资源版本不一致: {set(versions)}")
        self.assertIn(f'/static/js/forensics.js?v={versions[0]}', html)
        self.assertIn(f'/static/js/report.js?v={versions[0]}', html)

    def test_review_page_uses_same_asset_version(self):
        report_versions = re.findall(
            r"/static/(?:js|css)/[\w.-]+\?v=([\w]+)",
            _REPORT_HTML.read_text(encoding="utf-8"),
        )
        review_versions = re.findall(
            r"/static/(?:js|css)/[\w.-]+\?v=([\w]+)",
            _REVIEW_HTML.read_text(encoding="utf-8"),
        )
        self.assertTrue(review_versions, "review.html 未带静态资源版本号")
        self.assertEqual(len(set(review_versions)), 1)
        self.assertEqual(set(report_versions), set(review_versions),
                         "report 与 review 的静态资源版本必须一致")

    def test_fraud_patterns_are_wired_and_styled(self):
        js = _FORENSICS_JS.read_text(encoding="utf-8")
        self.assertIn("VC.renderFraudPatterns", js)
        self.assertIn("renderFraudPatterns(data)", js)
        self.assertIn("data.fraud_patterns", js)
        self.assertIn("data.report_facts", js)
        self.assertIn("fx-fraud-grid", js)
        # 判定依据面向一般用户：原始 JSON 必须消失，改为中文标签表格
        self.assertNotIn("JSON.stringify(p.facts", js)
        self.assertIn("FACT_LABELS", js)
        self.assertIn("查看判定数值与依据", js)
        self.assertIn("VC.renderPatternFacts", js)
        css = _REPORT_CSS.read_text(encoding="utf-8")
        self.assertIn(".fx-fraud-item", css)
        self.assertIn(".fx-fraud-grid", css)
        self.assertIn(".fx-fraud-table", css)
        self.assertNotIn(".fx-fraud-json", css)

    def test_shared_component_exposes_marker_api_and_dom_anchor(self):
        js = _FORENSICS_JS.read_text(encoding="utf-8")
        self.assertIn("VC.buildForensicMarkers", js)
        self.assertIn("VC.focusForensicSignal", js)
        self.assertIn("VC.focusForensicAnchor", js)
        self.assertIn("details.id = signalDomId(signal.id)", js)
        self.assertIn("item.id = findingDomId(i)", js)
        # 原文通读发现接线：单独一类、从 report_mining 取数、报告期还原
        self.assertIn('clue: { label: "原文通读线索"', js)
        self.assertIn("data.report_mining", js)
        self.assertIn("function findingReportDate", js)

    def test_report_js_wires_scatter_click_and_merge(self):
        js = _REPORT_JS.read_text(encoding="utf-8")
        self.assertIn('type: "scatter"', js)
        self.assertIn("yAxisIndex: 1", js)
        self.assertIn("applyForensicMarkers", js)
        self.assertIn("focusForensicSignal(params.data.signalId)", js)
        self.assertIn("focusForensicAnchor(params.data.anchorId)", js)
        self.assertIn("is-diamond", js)

    def test_css_has_legend_tooltip_and_flash_styles(self):
        css = _REPORT_CSS.read_text(encoding="utf-8")
        for cls in (".kline-legend", ".kl-tt-ev", ".fx-signal-flash", ".kl-legend-dot.is-diamond"):
            self.assertIn(cls, css)


@unittest.skipUnless(shutil.which("node"), "Node.js 不可用，跳过前端逻辑测试")
class MarkerLogicTests(unittest.TestCase):
    def test_build_forensic_markers(self):
        result = subprocess.run(
            ["node", "-e", _NODE_CHECK, str(_FORENSICS_JS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MARKER_LOGIC_OK", result.stdout)

    def test_render_audit_evidence(self):
        result = subprocess.run(
            ["node", "-e", _NODE_AUDIT_CHECK, str(_FORENSICS_JS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AUDIT_RENDER_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
