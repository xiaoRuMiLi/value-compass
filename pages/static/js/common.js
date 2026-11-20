/* 跨页面公共工具：代码符号归一化等。
 * 所有页面脚本通过 window.VC 调用。
 */
(function () {
  "use strict";

  window.VC = window.VC || {};

  /* 输入 "600519" / "000001" / "00700" / "AAPL" / "600519.SH"，
   * 归一化为 "<code>.<MARKET>"；已带后缀的保持不变。 */
  window.VC.normalize = function normalize(code) {
    const value = (code || "").trim().toUpperCase();
    if (value.includes(".")) return value;
    if (/^\d{6}$/.test(value)) {
      if (value[0] === "6") return `${value}.SH`;
      if ("03".includes(value[0])) return `${value}.SZ`;
      if ("48".includes(value[0])) return `${value}.BJ`;
    }
    if (/^\d{1,5}$/.test(value)) return `${value.padStart(5, "0")}.HK`;
    if (/^[A-Z]+$/.test(value)) return `${value}.US`;
    return value;
  };
})();
