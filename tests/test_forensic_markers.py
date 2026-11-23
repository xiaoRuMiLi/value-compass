"""财报关键点 → 日 K 线事件带：前端静态接线与事件点构建回归测试。

前端是原生 JS（IIFE + window.VC），逻辑放在 ``forensics.js`` 的纯函数里，
这里用 Node 的 vm 加载真实脚本后断言，避免把实现复制一份到测试里造成漂移。
"""

from __future__ import annotations

import shutil
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
console.log('MARKER_LOGIC_OK');
"""


class StaticWiringTests(unittest.TestCase):
    def test_report_page_has_event_band_container(self):
        html = _REPORT_HTML.read_text(encoding="utf-8")
        self.assertIn('id="klineMarkerLegend"', html)
        self.assertIn("财报关键点事件带", html)
        self.assertIn('src="/static/js/forensics.js?v=20261123b"', html)
        self.assertIn('src="/static/js/report.js?v=20261123b"', html)

    def test_review_page_uses_same_asset_version(self):
        self.assertIn("?v=20261123b", _REVIEW_HTML.read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    unittest.main()
