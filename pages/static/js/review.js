(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }
  function el(tag, text) { const n = document.createElement(tag); n.textContent = text ?? ""; return n; }

  async function render() {
    const a = await getJSON(`/api/stock/${symbol}/analysis`);
    document.getElementById("title").textContent = `${a.stock?.name || symbol} · 财报体检`;
    document.getElementById("summary").textContent = a.review.summary;
    const findings = document.getElementById("findings");
    for (const f of a.review.findings) {
      findings.appendChild(el("li", `[${f.severity}] ${f.type}：${f.detail}`));
    }
    for (const f of a.red_flags) {
      findings.appendChild(el("li", `[${f.severity}] ${f.flag_type}：${f.description}`));
    }
    document.getElementById("integrity").textContent = a.review.integrity_summary;
    const delisting = document.getElementById("delisting");
    if (a.delisting.triggers.length) {
      a.delisting.triggers.forEach((t) => delisting.appendChild(el("li", t)));
    } else {
      delisting.appendChild(el("li", "未触及退市指标"));
    }
  }
  render().catch(console.error);
})();
