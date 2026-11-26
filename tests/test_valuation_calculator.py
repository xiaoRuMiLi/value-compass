"""估值计算器前端回归测试：用 Node vm 加载真实脚本，验证各口径公式与来源切换。

前端是原生 JS（IIFE + window.ValuationCalculator），用假 DOM 提供字段取值，
断言计算逻辑与后端 analysis 层一致，避免实现漂移。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_CALC_JS = _ROOT / "pages" / "static" / "js" / "valuation-calculator.js"
_REPORT_JS = _ROOT / "pages" / "static" / "js" / "report.js"
_REPORT_HTML = _ROOT / "pages" / "report.html"

_NODE_SNIPPET = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const calcSrc = fs.readFileSync(process.argv[1], 'utf8');

// 最小 DOM：document.createElement / getElementById，字段值从 global FIELD_VALUES 取。
const fieldValues = JSON.parse(process.env.FIELD_VALUES || '{}');
function fakeEl(tag) {
  const e = {
    tagName: tag, className: '', textContent: '', type: '', id: '',
    value: '', step: '', title: '', href: '', target: '', rel: '', selected: false,
    classList: { add(){}, remove(){}, contains(){return false;} },
    children: [],
    appendChild(c){
      this.children.push(c);
      if (this.tagName === 'select' && c && c.selected) this.value = c.value;
      if (c && c.id && c.id.startsWith('calc-')) created[c.id] = c;
      return c;
    },
    addEventListener(){}, querySelectorAll(){ return []; },
  };
  let html = '';
  Object.defineProperty(e, 'innerHTML', {
    get(){ return html; },
    set(v){ html = v; if (v === '') this.children = []; },
  });
  return e;
}
const created = {};
const document = {
  createElement: fakeEl,
  body: { appendChild(){} },
  addEventListener(){},
  getElementById(id){
    if (created[id]) return created[id];
    const e = fakeEl('div'); e.id = id;
    created[id] = e; return e;
  },
};
const ctx = { window: {}, document, console, Math, Number, parseFloat, isNaN, JSON, parseInt };
vm.createContext(ctx);
vm.runInContext(calcSrc, ctx);
const VC = ctx.window.ValuationCalculator;
assert.ok(VC && typeof VC.open === 'function', 'window.ValuationCalculator.open 不存在');

// 打开每个口径，把字段值作为回填 data 传入（与真实页面一致），读取计算结果。
const results = {};
for (const method of ['ddm','dcf','adjusted_fcf','fcff','relative','graham']) {
  VC.open(method, fieldValues);
  const box = document.getElementById('calcResult');
  results[method] = box.children.map(c =>
    (c.children && c.children.length) ? c.children.map(x => x.textContent).join(' ') : c.textContent
  );
}
console.log(JSON.stringify(results));
"""


@unittest.skipUnless(shutil.which("node"), "需要 node 运行前端脚本")
class TestValuationCalculator(unittest.TestCase):
    def _run_js(self, field_values: dict) -> dict:
        import os
        env = dict(os.environ)
        env["FIELD_VALUES"] = json.dumps(field_values)
        proc = subprocess.run(
            ["node", "-e", _NODE_SNIPPET, str(_CALC_JS)],
            capture_output=True, text=True, env=env, timeout=30, cwd=str(_ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_ddm_formula_equivalent_to_backend(self):
        # DPS=2.58, g=0.02, r=0.09, price=72.55 => 37.59
        r = self._run_js({"dps": 2.5796852, "growth": 0.02, "discount": 0.09, "price": 72.55})
        row = next(x for x in r["ddm"] if x.startswith("合理价中枢"))
        self.assertIn("37.59", row)

    def test_adjusted_fcf_uses_standard_formula(self):
        fields = {
            "fcfSource": "derived", "ocf": 29706259919.13, "capex": 0,
            "interestIncome": 0, "interestExpense": 0,
            "shares": 3881612680.91, "growth": 0.04, "discount": 0.10,
            "terminalGrowth": 0.02, "years": 10, "price": 72.55,
            "inventory": 2677495038.33,
            "workingCapitalAlert": "应付项目阶段性增加，注意现金流质量",
        }
        r = self._run_js(fields)
        joined = " ".join(r["adjusted_fcf"])
        self.assertIn("经营活动现金流量净额", joined)
        self.assertIn("期初自由现金流", joined)
        self.assertIn("现金流质量提示", joined)
        self.assertIn("29706259919", joined.replace(",", ""))  # 未被存货扣减

    def test_source_switch_uses_direct_fcf(self):
        fields = {
            "fcfSource": "direct", "fcf": 9317113184.68,
            "ocf": 29706259919.13, "capex": 0,
            "shares": 3881612680.91, "growth": 0.04, "discount": 0.10,
            "terminalGrowth": 0.02, "years": 10, "price": 72.55,
        }
        r = self._run_js(fields)
        joined = " ".join(r["adjusted_fcf"]).replace(",", "")
        self.assertIn("直接输入", joined)
        self.assertIn("9317113184", joined)  # 起点是直接输入的 FCF，不是 OCF

    def test_working_capital_fields_do_not_change_value(self):
        base = {
            "fcfSource": "derived", "ocf": 29706259919.13, "capex": 0,
            "interestIncome": 0, "interestExpense": 0,
            "shares": 3881612680.91, "growth": 0.04, "discount": 0.10,
            "terminalGrowth": 0.02, "years": 10, "price": 72.55,
        }
        changed = dict(base, inventory=2e9, receivable=1e9, payable=-3e9, prepaid=5e8)
        center = lambda rows: next(x for x in rows if x.startswith("合理价中枢"))
        self.assertEqual(center(self._run_js(base)["adjusted_fcf"]),
                         center(self._run_js(changed)["adjusted_fcf"]))

    def test_fcff_bridge_matches_backend_logic(self):
        fields = {
            "ocf": 1000.0, "interestExpense": 50.0, "capex": 200.0,
            "cash": 300.0, "marketableSecurities": 20.0,
            "nonoperatingInvest": 500.0, "debt": 120.0,
            "minorityEquity": 90.0, "revenue": 1000.0,
            "excessCashRatio": 0.02, "nonoperatingDiscount": 0.8,
            "shares": 100.0, "growth": 0.05, "discount": 0.10,
            "terminalGrowth": 0.02, "years": 10, "price": 10.0,
        }
        joined = " ".join(self._run_js(fields)["fcff"])
        self.assertIn("企业自由现金流 FCFF", joined)
        self.assertIn("企业价值 EV", joined)
        self.assertIn("超额现金", joined)
        self.assertIn("300", joined.replace(",", ""))  # 超额现金 = 300 − 20 + 20
        self.assertIn("400", joined.replace(",", ""))  # 非经营投资 500 × 0.8

    def test_invalid_ddm_shows_error(self):
        r = self._run_js({"dps": 2.58, "growth": 0.10, "discount": 0.08, "price": 72.55})
        self.assertIn("参数无效", " ".join(r["ddm"]))

    def test_report_wires_calculator_buttons(self):
        js = _REPORT_JS.read_text(encoding="utf-8")
        html = _REPORT_HTML.read_text(encoding="utf-8")
        self.assertIn("calcButton", js)
        self.assertIn("calcDataFromResult", js)
        self.assertIn("window.ValuationCalculator", js)
        self.assertIn("valuation-calculator.js", html)


if __name__ == "__main__":
    unittest.main()
