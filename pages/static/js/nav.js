(function () {
  const { normalize } = window.VC;
  const SYMBOL_PATHS = [
    "/report",
    "/financials",
    "/review",
    "/integrity",
    "/calendar",
    "/calculator",
    "/products",
    "/compare",
    "/holders",
  ];
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
    ["/industry", "行业"],
    ["/screener", "筛选"],
    ["/holders-track", "持仓追踪"],
  ];

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

  // 顶部导航：品牌 + 搜索 + 市场级
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

  // 左右布局容器
  const app = document.createElement("div");
  app.className = "app";
  const sidebar = document.createElement("aside");
  sidebar.className = "sidebar";
  const content = document.createElement("div");
  content.className = "content";

  // 左侧栏：个股库
  sidebar.appendChild(el("h3", null, "个股库"));
  const stockList = document.createElement("div");
  stockList.className = "stock-list";
  stockList.appendChild(el("div", "stock-meta", "加载中…"));
  sidebar.appendChild(stockList);
  fetch("/api/stocks")
    .then((response) => (response.ok ? response.json() : []))
    .then((rows) => {
      stockList.innerHTML = "";
      for (const row of rows) {
        const item = document.createElement("a");
        item.href = `/report?symbol=${encodeURIComponent(row.symbol)}`;
        if (isSymbolPage && row.symbol === symbol) item.className = "active";
        item.appendChild(el("span", "stock-name", row.name));
        if (row.rating) {
          const tone = ["强烈推荐", "推荐"].includes(row.rating)
            ? "rating-rec"
            : row.rating === "观望" ? "rating-watch" : "rating-avoid";
          const badge = el("span", "stock-rating " + tone, row.rating);
          if (row.score != null) badge.title = `综合得分 ${row.score}`;
          item.appendChild(badge);
        }
        item.appendChild(el("span", "stock-meta", row.price == null ? "-" : row.price.toFixed(2)));
        stockList.appendChild(item);
      }
    })
    .catch(() => {
      stockList.innerHTML = "";
    });

  // 右侧内容：移动 main 进来
  document.querySelectorAll("body > main").forEach((node) => content.appendChild(node));

  // 个股信息条 + 标签行（个股页）
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

    const tabsbar = document.createElement("div");
    tabsbar.className = "stock-tabsbar";
    const tabs = document.createElement("div");
    tabs.className = "stock-tabs";
    for (const [path, name] of SYMBOL_TABS) {
      const item = link(`${path}?symbol=${encodeURIComponent(symbol)}`, name);
      if (currentPath === path) item.className = "active";
      tabs.appendChild(item);
    }
    tabsbar.appendChild(tabs);
    tabsbar.appendChild(el("div", "tab-sep"));
    const tools = document.createElement("div");
    tools.className = "stock-tabs";
    for (const [path, name] of [
      ["/compare", "对比"],
      ["/holders", "持仓"],
    ]) {
      const item = link(`${path}?symbol=${encodeURIComponent(symbol)}`, name);
      if (currentPath === path) item.className = "active";
      tools.appendChild(item);
    }
    tabsbar.appendChild(tools);
    content.prepend(bar, tabsbar);

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

  app.appendChild(sidebar);
  app.appendChild(content);
  document.body.appendChild(app);
})();
