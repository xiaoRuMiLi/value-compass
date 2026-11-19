(function () {
  const SYMBOL_PAGES = [
    ["/report", "报告"],
    ["/financials", "财务"],
    ["/review", "体检"],
    ["/integrity", "诚信"],
    ["/calendar", "日历"],
    ["/calculator", "计算器"],
    ["/products", "产品"],
  ];
  const GLOBAL_PAGES = [
    ["/compare", "对比"],
    ["/screener", "筛选"],
    ["/holders", "持仓"],
    ["/industry", "行业"],
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

  const symbol = normalize(new URLSearchParams(location.search).get("symbol") || "600519");
  const currentPath = location.pathname;

  const nav = document.createElement("nav");
  nav.className = "topnav";

  const brand = document.createElement("a");
  brand.className = "brand";
  brand.href = "/";
  brand.textContent = "价值罗盘";
  nav.appendChild(brand);

  const symbolGroup = document.createElement("span");
  symbolGroup.className = "nav-group";
  symbolGroup.appendChild(Object.assign(document.createElement("span"), { className: "nav-label", textContent: "个股" }));
  for (const [path, name] of SYMBOL_PAGES) {
    const link = document.createElement("a");
    link.href = `${path}?symbol=${encodeURIComponent(symbol)}`;
    link.textContent = name;
    if (currentPath === path) link.className = "active";
    symbolGroup.appendChild(link);
  }
  nav.appendChild(symbolGroup);

  const globalGroup = document.createElement("span");
  globalGroup.className = "nav-group";
  globalGroup.appendChild(Object.assign(document.createElement("span"), { className: "nav-label", textContent: "市场" }));
  for (const [path, name] of GLOBAL_PAGES) {
    const link = document.createElement("a");
    link.href = path;
    link.textContent = name;
    if (currentPath === path) link.className = "active";
    globalGroup.appendChild(link);
  }
  nav.appendChild(globalGroup);

  const form = document.createElement("form");
  form.className = "nav-search";
  const input = document.createElement("input");
  input.placeholder = "代码 600519 / 00700 / BABA";
  input.value = symbol;
  form.appendChild(input);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const target = normalize(input.value);
    if (target) location.href = `/report?symbol=${encodeURIComponent(target)}`;
  });
  nav.appendChild(form);

  document.body.prepend(nav);
})();
