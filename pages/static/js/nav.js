(function () {
  const SYMBOL_PATHS = ["/report", "/financials", "/review", "/integrity", "/calendar", "/calculator", "/products"];
  const SYMBOL_TABS = [
    ["/report", "报告"],
    ["/financials", "财务"],
    ["/review", "体检"],
    ["/integrity", "诚信"],
    ["/calendar", "日历"],
    ["/calculator", "计算器"],
    ["/products", "产品"],
  ];
  const MARKET_LINKS = [
    ["/stocks", "个股库"],
    ["/industry", "行业"],
    ["/compare", "对比"],
    ["/screener", "筛选"],
    ["/holders", "持仓"],
  ];

  function normalize(code) {
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
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function link(href, text) {
    const node = el("a", null, text);
    node.href = href;
    return node;
  }

  const symbol = normalize(new URLSearchParams(location.search).get("symbol") || "600519");
  const currentPath = location.pathname;
  const isSymbolPage = SYMBOL_PATHS.includes(currentPath);

  // 全局顶栏
  const top = document.createElement("nav");
  top.className = "topnav";
  const brand = link("/", "价值罗盘");
  brand.className = "brand";
  top.appendChild(brand);

  const search = document.createElement("form");
  search.className = "nav-search";
  const input = document.createElement("input");
  input.placeholder = "代码 600519 / 00700 / BABA";
  input.value = isSymbolPage ? symbol : "";
  search.appendChild(input);
  search.addEventListener("submit", (event) => {
    event.preventDefault();
    const target = normalize(input.value);
    if (target) location.href = `/report?symbol=${encodeURIComponent(target)}`;
  });
  top.appendChild(search);

  for (const [path, name] of MARKET_LINKS) {
    const item = link(path, name);
    if (currentPath === path) item.className = "active";
    top.appendChild(item);
  }
  document.body.prepend(top);

  // 个股标签栏（仅个股页）
  if (isSymbolPage) {
    const bar = document.createElement("div");
    bar.className = "stockbar";
    const info = document.createElement("div");
    info.className = "stock-info";
    info.appendChild(el("span", "stock-name", symbol));
    info.appendChild(el("span", "stock-price", "…"));
    info.appendChild(el("span", "stock-pct", ""));
    info.appendChild(el("span", "stock-score", ""));
    bar.appendChild(info);

    const tabs = document.createElement("div");
    tabs.className = "stock-tabs";
    for (const [path, name] of SYMBOL_TABS) {
      const item = link(`${path}?symbol=${encodeURIComponent(symbol)}`, name);
      if (currentPath === path) item.className = "active";
      tabs.appendChild(item);
    }
    bar.appendChild(tabs);
    top.after(bar);

    fetch(`/api/stock/${symbol}/analysis`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data) return;
        info.innerHTML = "";
        info.appendChild(el("span", "stock-name", data.stock?.name || symbol));
        const price = data.snapshot?.price;
        info.appendChild(el("span", "stock-price", price == null ? "-" : price.toFixed(2)));
        const pct = data.snapshot?.pct_change;
        const pctNode = el("span", "stock-pct", pct == null ? "" : `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`);
        if (pct != null) pctNode.className += pct >= 0 ? " up" : " down";
        info.appendChild(pctNode);
        const score = data.score?.total;
        info.appendChild(el("span", "stock-score", score == null ? "" : `${score} ${data.score.rating}`));
      })
      .catch(() => {});
  }
})();
