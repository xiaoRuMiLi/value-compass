/* 财报阅读入口：统一生成阅读页链接，避免各页面重复拼参数。
 * docId 来自 /api/stock/{symbol}/documents 或抽取字段，前端永不接触 local_path。
 */
(function () {
  "use strict";

  window.VC = window.VC || {};

  window.VC.readerUrl = function readerUrl(docId, options) {
    const opts = options || {};
    const query = new URLSearchParams();
    const symbol = opts.symbol || new URLSearchParams(location.search).get("symbol") || "600519";
    query.set("symbol", symbol);
    if (docId) query.set("doc", String(docId));
    if (opts.q) query.set("q", String(opts.q));
    if (opts.keywords) query.set("keywords", String(opts.keywords));
    if (opts.section) query.set("section", String(opts.section));
    if (opts.from) query.set("from", String(opts.from));
    query.set("mode", opts.mode || "pdf"); // 默认打开原始 PDF；失败时页面自动降级文字版
    return `/document?${query.toString()}`;
  };

  window.VC.openReader = function openReader(docId, options) {
    window.open(window.VC.readerUrl(docId, options), "_blank", "noopener,noreferrer");
  };
})();
