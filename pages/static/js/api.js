/* 统一 API 封装：fetch → JSON，集中处理 HTTP 错误。
 * 所有页面脚本通过 window.VC.getJSON 调用，不再各自实现。
 */
(function () {
  "use strict";

  window.VC = window.VC || {};

  window.VC.getJSON = async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${url}`);
    return response.json();
  };
})();
