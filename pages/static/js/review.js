/* 财报体检独立页：保留原有量化摘要，主体交给共享的 forensics.js 渲染。 */
(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  const { getJSON } = window.VC;

  function el(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text ?? "";
    return node;
  }

  async function renderBasics() {
    const a = await getJSON(`/api/stock/${symbol}/analysis`);
    const back = document.getElementById("backReport");
    if (back) back.href = `/report?symbol=${encodeURIComponent(symbol)}`;
    const title = document.getElementById("pageTitle");
    if (title && a.stock && a.stock.name) title.textContent = `${a.stock.name} · 财报深度体检`;
    document.title = title ? title.textContent : "财报深度体检";

    document.getElementById("summary").textContent = a.review.summary;
    const findings = document.getElementById("findings");
    for (const f of a.review.findings || []) {
      findings.appendChild(el("li", `[${f.severity}] ${f.type}：${f.detail}`));
    }
    for (const f of a.red_flags || []) {
      findings.appendChild(el("li", `[${f.severity}] ${f.flag_type}：${f.description}`));
    }
    document.getElementById("integrity").textContent = a.review.integrity_summary;
    const delisting = document.getElementById("delisting");
    const triggers = (a.delisting && a.delisting.triggers) || [];
    if (triggers.length) {
      triggers.forEach((t) => delisting.appendChild(el("li", t)));
    } else {
      delisting.appendChild(el("li", "未触及退市指标"));
    }
  }

  if (window.VC.loadForensics) {
    const box = document.getElementById("forensics");
    window.VC.loadForensics(box, symbol);
    const refreshBtn = document.getElementById("forensicsRefresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshBtn.disabled = true;
        window.VC.loadForensics(box, symbol, { refresh: true }).finally(() => {
          refreshBtn.disabled = false;
        });
      });
    }
  }

  renderBasics().catch(console.error);
})();
