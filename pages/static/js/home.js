(function () {
  const SYMBOL_PAGES = [
    ["/report", "报告"],
    ["/financials", "财务全景"],
    ["/review", "财报体检"],
    ["/integrity", "诚信档案"],
    ["/calendar", "事件日历"],
    ["/calculator", "计算器"],
    ["/products", "产品价格"],
  ];
  const GLOBAL_PAGES = [
    ["/compare", "多股对比"],
    ["/screener", "条件筛选"],
    ["/holders", "持仓追踪"],
    ["/industry", "行业热度"],
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

  document.getElementById("search").addEventListener("submit", (event) => {
    event.preventDefault();
    const target = normalize(document.getElementById("q").value);
    if (target) location.href = `/report?symbol=${encodeURIComponent(target)}`;
  });

  const box = document.getElementById("links");
  const addGroup = (title, pages, symbol) => {
    const group = document.createElement("div");
    group.className = "link-group";
    const label = document.createElement("h2");
    label.textContent = title;
    group.appendChild(label);
    for (const [path, name] of pages) {
      const link = document.createElement("a");
      link.href = symbol ? `${path}?symbol=${encodeURIComponent(symbol)}` : path;
      link.textContent = name;
      group.appendChild(link);
    }
    box.appendChild(group);
  };

  addGroup("个股分析", SYMBOL_PAGES, "600519.SH");
  addGroup("市场全景", GLOBAL_PAGES, null);
})();
