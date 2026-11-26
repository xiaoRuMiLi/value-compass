/* 财报原文阅读器：章节目录 + 分块加载 + 引文定位高亮。
 * 所有文本来自 /api/document/*，服务端不下发绝对路径；这里只做展示与滚动。
 */
(function () {
  "use strict";

  const { getJSON, normalize } = window.VC;
  const params = new URLSearchParams(location.search);
  const symbol = normalize(params.get("symbol") || "600519");
  const docParam = Number.parseInt(params.get("doc") || "0", 10);

  const state = {
    docId: docParam,
    meta: null,
    sections: [],
    matches: [],
    matchIndex: 0,
    windowStart: 0,
    windowEnd: 0,
    query: params.get("q") || "",
    docs: [],
    docIndex: -1,
    mode: params.get("mode") || "pdf",
    pdfLib: null,
    pdfDoc: null,
    pdfPage: 0,
    pdfScale: 1.2,
    pdfFitMode: "page", // page=整页 / width=适宽 / manual=手动缩放
    pdfRenderTask: null,
    pdfRenderSeq: 0,
    pdfMatches: [],
    pdfMatchIndex: 0,
    pdfOutline: [],
    pdfOutlineLoaded: false,
    textLocateKey: "",
  };

  const $ = (id) => document.getElementById(id);

  // 文档列表按时间正序：同年 Q1 → 半年报 → Q3 → 年报。
  const DOC_ORDER = { q1_report: 0, semi_report: 1, q3_report: 2, annual_report: 3 };
  function docCompare(a, b) {
    const pa = String(a.period || "").padStart(4, "0") + String(DOC_ORDER[a.doc_type] ?? 9).padStart(2, "0");
    const pb = String(b.period || "").padStart(4, "0") + String(DOC_ORDER[b.doc_type] ?? 9).padStart(2, "0");
    return pa.localeCompare(pb);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setStatus(level, text) {
    const box = $("locateStatus");
    box.hidden = false;
    box.dataset.level = level;
    box.textContent = text;
  }

  function locateKey() {
    return [state.query, params.get("section") || "", params.get("keywords") || ""].join("\u0001");
  }

  function renderPicker(docs) {
    $("pickerPanel").hidden = false;
    const host = $("docPicker");
    host.innerHTML = "";
    const usable = docs.filter((d) => d.has_text);
    if (!usable.length) {
      host.textContent = "该股票暂无可阅读的财报全文";
      return;
    }
    for (const doc of usable) {
      const row = document.createElement("div");
      row.className = "doc-picker-item";
      const link = document.createElement("a");
      link.href = `/document?symbol=${encodeURIComponent(symbol)}&doc=${doc.id}`;
      link.textContent = doc.title || `${doc.doc_type} ${doc.period}`;
      row.appendChild(link);
      const meta = document.createElement("span");
      meta.className = "doc-picker-meta";
      meta.textContent = `${doc.period || "—"} · ${doc.doc_type}`;
      row.appendChild(meta);
      host.appendChild(row);
    }
  }

  function renderDocNav() {
    const total = state.docs.length;
    const pos = state.docIndex;
    $("docPosition").textContent = pos >= 0 && total ? `第 ${pos + 1} / ${total} 期` : "期次清单未加载";
    $("prevDoc").disabled = pos <= 0;
    $("nextDoc").disabled = pos < 0 || pos >= total - 1;
    if (pos >= 0) {
      const doc = state.docs[pos];
      $("docPeriodBadge").hidden = false;
      $("docPeriodBadge").textContent = `${doc.period || "—"} · ${doc.doc_type || "财报"}`;
      $("prevDoc").title = pos > 0 ? `上一期：${state.docs[pos - 1].title || ""}` : "已是更早一期";
      $("nextDoc").title = pos < total - 1 ? `下一期：${state.docs[pos + 1].title || ""}` : "已是最新一期";
    }
  }

  function goDoc(delta) {
    const target = state.docs[state.docIndex + delta];
    if (!target) return;
    location.href = `/document?symbol=${encodeURIComponent(symbol)}&doc=${target.id}`;
  }

  function renderToc() {
    const host = $("docToc");
    host.innerHTML = "";
    const filter = ($("tocFilter").value || "").trim();
    for (const section of state.sections) {
      if (filter && !section.heading.includes(filter)) continue;
      const link = document.createElement("a");
      link.href = "#";
      link.textContent = section.heading;
      link.title = section.heading;
      link.dataset.start = String(section.start);
      link.dataset.index = String(section.index);
      link.addEventListener("click", (event) => {
        event.preventDefault();
        loadAt(section.start);
        host.querySelectorAll("a.active").forEach((node) => node.classList.remove("active"));
        link.classList.add("active");
      });
      host.appendChild(link);
    }
  }

  function updateMatchLabel() {
    $("matchIndex").textContent = state.matches.length
      ? `${state.matchIndex + 1} / ${state.matches.length}`
      : "—";
    $("prevMatch").disabled = !state.matches.length;
    $("nextMatch").disabled = !state.matches.length;
  }

  function renderText() {
    const host = $("docText");
    const chunk = state.currentChunk;
    if (!chunk) return;
    const base = chunk.start;
    const marks = state.matches
      .map((m, i) => ({ ...m, i }))
      .filter((m) => m.end > base && m.start < chunk.end)
      .sort((a, b) => a.start - b.start);

    let html = "";
    let cursor = base;
    for (const mark of marks) {
      const start = Math.max(base, mark.start);
      const end = Math.min(chunk.end, mark.end);
      if (start < cursor) continue;
      html += esc(chunk.text.slice(cursor - base, start - base));
      html += `<mark data-match="${mark.i}" class="${mark.i === state.matchIndex ? "doc-current" : ""}">`
        + esc(chunk.text.slice(start - base, end - base)) + "</mark>";
      cursor = end;
    }
    html += esc(chunk.text.slice(cursor - base));
    host.innerHTML = html;

    $("docProgress").textContent = `${chunk.start + 1}–${chunk.end} / ${chunk.total_chars} 字`;
    updateMatchLabel();

    const current = host.querySelector("mark.doc-current");
    if (current) {
      host.scrollTop = Math.max(0, current.offsetTop - host.clientHeight / 2);
    }
  }

  async function loadAt(start) {
    if (!state.meta) return;
    const maxStart = Math.max(0, state.meta.total_chars - 1);
    const begin = Math.max(0, Math.min(Math.floor(start), maxStart));
    try {
      const chunk = await getJSON(
        `/api/document/${state.docId}/text?start=${begin}&length=20000`
      );
      state.windowStart = chunk.start;
      state.windowEnd = chunk.end;
      state.currentChunk = chunk;
      renderText();
    } catch (err) {
      setStatus("fallback", `原文分块加载失败：${err.message}`);
    }
  }

  function focusMatch(index) {
    if (!state.matches.length) return;
    state.matchIndex = (index + state.matches.length) % state.matches.length;
    const target = state.matches[state.matchIndex];
    if (target.start < state.windowStart || target.start >= state.windowEnd) {
      loadAt(Math.max(0, target.start - 6000));
    } else {
      renderText();
    }
  }

  async function applyLocate() {
    const query = state.query || "";
    if (!query && !params.get("section")) return;
    const url = `/api/document/${state.docId}/locate?q=${encodeURIComponent(query)}`
      + `&keywords=${encodeURIComponent(params.get("keywords") || "")}`
      + `&section=${encodeURIComponent(params.get("section") || "")}`;
    const result = await getJSON(url);
    state.matches = result.matches || [];
    state.matchIndex = 0;
    const strategy = {
      exact: "已按引文精确匹配定位",
      normalized: "引文存在断行或空格差异，已按归一化文本定位",
      prefix: "引文过长，已按开头片段定位",
      keyword: "引文未命中，已按关键词定位",
    }[result.strategy] || "未定位到引文原文";

    if (state.matches.length) {
      setStatus("ok", `${strategy} · 共 ${state.matches.length} 处，已高亮并滚动到第一处`);
    } else if (result.section_index != null && state.sections[result.section_index]) {
      const section = state.sections[result.section_index];
      setStatus("fallback", `${result.note} · 已定位到章节「${section.heading}」`);
      await loadAt(section.start);
      return;
    } else {
      setStatus("fallback", result.note || "未定位到引文原文，请按左侧章节自行核对");
    }
    focusMatch(0);
  }

  // 文字版按需初始化：从 PDF 版切过来时正文尚未加载，这里统一补齐定位与首段。
  async function ensureTextContent() {
    const key = locateKey();
    if (state.textLocateKey !== key) {
      state.textLocateKey = key;
      if (state.query || params.get("section") || params.get("keywords")) {
        await applyLocate().catch((err) => setStatus("fallback", `文字版定位失败：${err.message}`));
      } else {
        state.matches = [];
        state.matchIndex = 0;
        updateMatchLabel();
      }
    }
    if (!state.currentChunk) {
      await loadAt(Number.parseInt(params.get("start") || "0", 10) || 0);
    } else {
      renderText();
    }
  }

  // ===== PDF 原版阅读：页码 + 页面内矩形高亮 + 可点击目录，失败自动降级文字版 =====
  async function ensurePdfJs() {
    if (state.pdfLib) return state.pdfLib;
    const lib = await import("/static/vendor/pdfjs/pdf.min.mjs");
    lib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.mjs";
    state.pdfLib = lib;
    return lib;
  }

  async function openPdf() {
    if (state.pdfDoc) return state.pdfDoc;
    const lib = await ensurePdfJs();
    state.pdfDoc = await lib.getDocument(`/api/document/${state.docId}/pdf`).promise;
    return state.pdfDoc;
  }

  function markActivePdfToc(page) {
    const host = $("pdfToc");
    host.querySelectorAll("button.active").forEach((node) => node.classList.remove("active"));
    const active = host.querySelector(`button[data-page="${page}"]`);
    if (!active) return;
    active.classList.add("active");
    active.scrollIntoView({ block: "nearest" });
  }

  function renderPdfToc() {
    const host = $("pdfToc");
    host.innerHTML = "";
    const filter = ($("pdfTocFilter").value || "").trim();
    let shown = 0;
    for (const item of state.pdfOutline) {
      if (filter && !item.title.includes(filter)) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.title;
      button.title = `第 ${item.page} 页`;
      button.dataset.page = String(item.page);
      button.dataset.level = String(item.level || 1);
      button.addEventListener("click", () => {
        renderPdfPage(item.page).catch((err) => setStatus("fallback", `页面渲染失败：${err.message}`));
        markActivePdfToc(item.page);
      });
      host.appendChild(button);
      shown += 1;
    }
    if (!shown) {
      const empty = document.createElement("div");
      empty.className = "pdf-toc-empty";
      empty.textContent = filter ? "没有匹配的目录项" : "暂无目录，可用翻页按钮浏览";
      host.appendChild(empty);
    }
    markActivePdfToc(state.pdfPage);
  }

  async function ensurePdfOutline() {
    if (state.pdfOutlineLoaded) {
      renderPdfToc();
      return;
    }
    const result = await getJSON(`/api/document/${state.docId}/pdf-outline`);
    state.pdfOutline = result.items || [];
    state.pdfOutlineLoaded = true;
    $("pdfTocMeta").textContent = result.source === "bookmark"
      ? `PDF 原生书签 · ${state.pdfOutline.length} 项`
      : result.source === "pages"
        ? `按页首标题生成 · ${state.pdfOutline.length} 项`
        : "暂无目录";
    renderPdfToc();
  }

  function updatePdfMatchLabel() {
    $("matchIndex").textContent = state.pdfMatches.length
      ? `${state.pdfMatchIndex + 1} / ${state.pdfMatches.length}`
      : "—";
    $("prevMatch").disabled = !state.pdfMatches.length;
    $("nextMatch").disabled = !state.pdfMatches.length;
  }

  function drawPdfHighlights(page, viewport) {
    const host = $("pdfHighlights");
    host.innerHTML = "";
    host.style.width = viewport.width + "px";
    host.style.height = viewport.height + "px";
    const onPage = state.pdfMatches.filter((m) => m.page === state.pdfPage);
    onPage.forEach((match, i) => {
      const boxRect = viewport.convertToViewportRectangle(match.rect);
      const box = document.createElement("div");
      box.className = "pdf-hl" + (i === state.pdfMatchIndex ? " pdf-hl-active" : "");
      box.style.left = Math.min(boxRect[0], boxRect[2]) + "px";
      box.style.top = Math.min(boxRect[1], boxRect[3]) + "px";
      box.style.width = Math.abs(boxRect[2] - boxRect[0]) + "px";
      box.style.height = Math.abs(boxRect[3] - boxRect[1]) + "px";
      box.title = match.snippet || "";
      host.appendChild(box);
    });
    if (onPage.length) {
      const active = host.querySelector(".pdf-hl-active") || host.firstChild;
      if (active) {
        const wrap = $("pdfPages");
        wrap.scrollTop = Math.max(0, active.offsetTop + $("pdfPageWrap").offsetTop - wrap.clientHeight / 3);
      }
    }
  }

  async function fitScaleFor(page) {
    const host = $("pdfPages");
    const base = page.getViewport({ scale: 1 });
    const width = Math.max(240, host.clientWidth - 24);
    const height = Math.max(320, host.clientHeight - 24);
    // 适宽模式只按宽度缩放：文字更大且无横向滚动，纵向可滚动。
    const scale = state.pdfFitMode === "width"
      ? width / base.width
      : Math.min(width / base.width, height / base.height);
    return Math.max(0.35, Math.min(2.5, scale));
  }

  async function renderPdfPage(pageNum) {
    const pdf = await openPdf();
    const target = Math.max(1, Math.min(pageNum || 1, pdf.numPages));
    const seq = ++state.pdfRenderSeq;
    state.pdfPage = target;
    const page = await pdf.getPage(target);
    if (state.pdfFitMode !== "manual") state.pdfScale = await fitScaleFor(page);
    const viewport = page.getViewport({ scale: state.pdfScale });

    // 服务端 MuPDF 按页渲染：规避浏览器 PDF.js 对港交所报告嵌入字体的兼容问题。
    // 图片按 1.5~2 倍像素输出，再按 CSS 尺寸显示，文字边缘更锐利。
    const dpr = Math.min(2, Math.max(1.5, window.devicePixelRatio || 1));
    const renderScale = state.pdfScale * dpr;
    const img = $("pdfImage");
    img.style.width = viewport.width + "px";
    img.style.height = viewport.height + "px";
    const url = `/api/document/${state.docId}/pdf-page?page=${target}`
      + `&scale=${renderScale.toFixed(3)}`;
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error("页面图片加载失败"));
      img.src = url;
    });
    if (seq !== state.pdfRenderSeq) return; // 已有更新的翻页请求，丢弃旧结果

    $("pdfPage").textContent = `${target} / ${pdf.numPages}`;
    $("pdfZoom").textContent = Math.round(state.pdfScale * 100) + "%";
    $("pdfFit").classList.toggle("active", state.pdfFitMode === "page");
    $("pdfFitWidth").classList.toggle("active", state.pdfFitMode === "width");
    $("pdfProgress").textContent = `原始 PDF · ${pdf.numPages} 页 · 清晰渲染`;
    updatePdfMatchLabel();
    drawPdfHighlights(page, viewport);
    markActivePdfToc(target);
  }

  // 翻页并复位滚动位置：下一页回到顶部，上一页落到底部，衔接连续阅读。
  async function turnPdfPage(step) {
    const next = state.pdfPage + step;
    if (next < 1 || (state.pdfDoc && next > state.pdfDoc.numPages)) return;
    await renderPdfPage(next);
    const host = $("pdfPages");
    host.scrollTop = step > 0 ? 0 : host.scrollHeight;
  }

  async function applyPdfLocate() {
    const url = `/api/document/${state.docId}/pdf-locate?q=${encodeURIComponent(state.query || "")}`
      + `&keywords=${encodeURIComponent(params.get("keywords") || "")}`;
    const result = await getJSON(url);
    state.pdfMatches = result.matches || [];
    state.pdfMatchIndex = 0;
    if (!state.pdfMatches.length) {
      updatePdfMatchLabel();
      setStatus("fallback", result.note || "PDF 中未找到该引文，可在文字版中继续查找");
      return false;
    }
    const labels = {
      exact: "已按引文精确匹配定位",
      normalized: "引文存在断行差异，已按归一化文本定位",
      prefix: "引文较长，已按开头片段定位",
      keyword: "引文未命中，已按关键词定位",
    };
    setStatus("ok", `${labels[result.strategy] || "PDF 定位"} · 共 ${state.pdfMatches.length} 处 · 已跳到第 ${state.pdfMatches[0].page} 页并高亮`);
    await renderPdfPage(state.pdfMatches[0].page);
    return true;
  }

  function pdfFocusMatch(index) {
    if (!state.pdfMatches.length) return;
    const n = state.pdfMatches.length;
    state.pdfMatchIndex = (index + n) % n;
    updatePdfMatchLabel();
    const target = state.pdfMatches[state.pdfMatchIndex];
    renderPdfPage(target.page).catch((err) => setStatus("fallback", `页面渲染失败：${err.message}`));
  }

  function applyModeVisibility(mode) {
    const isPdf = mode === "pdf";
    state.mode = mode;
    $("modePdf").classList.toggle("active", isPdf);
    $("modeText").classList.toggle("active", !isPdf);
    $("pdfMode").hidden = !isPdf;
    $("textMode").hidden = isPdf;
    $("pdfTools").hidden = !isPdf;
    $("textTools").hidden = isPdf;
    $("pdfTocBlock").hidden = !isPdf;
    $("textTocBlock").hidden = isPdf;
  }

  async function switchMode(mode, opts) {
    const options = opts || {};
    applyModeVisibility(mode);

    if (mode === "text") {
      try {
        await ensureTextContent();
      } catch (err) {
        setStatus("fallback", `文字版加载失败：${err.message}`);
      }
      return;
    }

    try {
      await openPdf();
      // 目录是辅助功能，接口失败不应影响正文阅读。
      try {
        await ensurePdfOutline();
      } catch (err) {
        $("pdfTocMeta").textContent = "目录加载失败，可继续翻页阅读";
      }
      if (!options.skipRender) await renderPdfPage(state.pdfPage || 1);
    } catch (err) {
      setStatus("fallback", `PDF 阅读器加载失败，已切换文字版：${err.message}`);
      applyModeVisibility("text");
      await ensureTextContent().catch(() => {});
    }
  }

  const docsPromise = getJSON(`/api/stock/${encodeURIComponent(symbol)}/documents`)
    .then((docs) => {
      state.docs = (docs || []).filter((d) => d.has_text).sort(docCompare);
      return state.docs;
    })
    .catch(() => []);

  async function loadDoc() {
    try {
      state.meta = await getJSON(`/api/document/${state.docId}`);
      await docsPromise;
      state.docIndex = state.docs.findIndex((d) => d.id === state.docId);
    } catch (err) {
      setStatus("fallback", `财报文档加载失败：${err.message}`);
      return;
    }
    const meta = state.meta;
    $("pickerPanel").hidden = true;
    $("readerPanel").hidden = false;
    $("docTitle").textContent = meta.title || "财报原文";
    $("backReview").href = `/review?symbol=${encodeURIComponent(symbol)}`;
    $("docMeta").textContent = `${meta.symbol} · ${meta.period || "—"} · ${meta.doc_type} · 全文 ${meta.total_chars.toLocaleString()} 字 · ${meta.sections.length} 个章节`;
    renderDocNav();
    const link = $("downloadPdf");
    if (meta.has_pdf) {
      link.hidden = false;
      link.href = `/api/document/${state.docId}/pdf`;
      link.removeAttribute("aria-disabled");
      link.title = "下载归档的原始 PDF 文件";
    } else {
      // 无归档 PDF 时按钮保留可见但置灰，避免用户以为控件消失。
      link.hidden = false;
      link.href = "#";
      link.setAttribute("aria-disabled", "true");
      link.title = "该文档未归档原始 PDF";
    }
    state.sections = meta.sections || [];
    renderToc();
    $("searchInput").value = state.query;
    if (!meta.has_pdf) state.mode = "text";
    if (state.mode === "pdf") {
      await switchMode("pdf", { skipRender: true }); // 加载失败会自行降级为 text
    }
    if (state.mode === "pdf") {
      if (state.query || params.get("keywords")) {
        const located = await applyPdfLocate();
        if (!located) await renderPdfPage(1);
      } else {
        await renderPdfPage(1);
      }
    } else {
      await switchMode("text"); // 统一走文字版初始化：定位 + 首段渲染
    }
  }

  // ===== 下载原始 PDF：用 fetch + Blob 主动触发，避免内置预览面板拦截 <a> 导航下载 =====
  function downloadName() {
    const m = state.meta || {};
    return `${m.symbol || "report"}_${m.period || "report"}_${m.doc_type || "pdf"}.pdf`;
  }

  $("downloadPdf").addEventListener("click", async (ev) => {
    ev.preventDefault();
    if (!state.docId || (state.meta && !state.meta.has_pdf)) {
      setStatus("fallback", "当前文档没有可下载的原始 PDF");
      return;
    }
    const url = `/api/document/${state.docId}/pdf`;
    setStatus("ok", "正在准备下载原始 PDF…");
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = downloadName();
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      setStatus("ok", "下载已开始；若浏览器未弹出保存提示，请检查下载目录或允许本站下载。");
    } catch (err) {
      setStatus("fallback", `下载失败：${err.message}。可点此链接直接打开：${url}`);
    }
  });

  $("searchBtn").addEventListener("click", async () => {
    state.query = $("searchInput").value.trim();
    if (!state.query) {
      setStatus("fallback", "请输入要查找的原文片段或关键词");
      return;
    }
    try {
      if (state.mode === "pdf") await applyPdfLocate();
      else await applyLocate();
    } catch (err) {
      setStatus("fallback", `定位失败：${err.message}`);
    }
  });

  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("searchBtn").click();
  });

  $("tocFilter").addEventListener("input", renderToc);
  $("pdfTocFilter").addEventListener("input", renderPdfToc);
  $("prevMatch").addEventListener("click", () => {
    if (state.mode === "pdf") pdfFocusMatch(state.pdfMatchIndex - 1);
    else focusMatch(state.matchIndex - 1);
  });
  $("nextMatch").addEventListener("click", () => {
    if (state.mode === "pdf") pdfFocusMatch(state.pdfMatchIndex + 1);
    else focusMatch(state.matchIndex + 1);
  });
  $("loadEarlier").addEventListener("click", () => loadAt(Math.max(0, state.windowStart - 16000)));
  $("loadLater").addEventListener("click", () => loadAt(state.windowStart + 16000));
  $("prevDoc").addEventListener("click", () => goDoc(-1));
  $("nextDoc").addEventListener("click", () => goDoc(1));
  $("modePdf").addEventListener("click", async () => {
    if (!state.meta || !state.meta.has_pdf) {
      setStatus("fallback", "该文档未归档原始 PDF");
      return;
    }
    await switchMode("pdf");
    if (state.mode === "pdf" && state.query && !state.pdfMatches.length) {
      try { await applyPdfLocate(); } catch (err) { setStatus("fallback", `PDF 定位失败：${err.message}`); }
    }
  });
  $("modeText").addEventListener("click", () => switchMode("text"));
  $("pdfPrev").addEventListener("click", () => {
    turnPdfPage(-1).catch((err) => setStatus("fallback", err.message));
  });
  $("pdfNext").addEventListener("click", () => {
    turnPdfPage(1).catch((err) => setStatus("fallback", err.message));
  });

  // 滚动到底部继续下滚 → 下一页；滚动到顶部继续上滚 → 上一页。
  // 加锁避免翻页动画未完成时连续触发跳页；仅在到达边界且方向一致时接管滚轮。
  let pdfTurning = false;
  $("pdfPages").addEventListener("wheel", (event) => {
    if (state.mode !== "pdf" || !state.pdfDoc || pdfTurning) return;
    const host = $("pdfPages");
    const atBottom = host.scrollTop + host.clientHeight >= host.scrollHeight - 2;
    const atTop = host.scrollTop <= 2;
    const goingDown = event.deltaY > 0;
    const goingUp = event.deltaY < 0;
    if (!((goingDown && atBottom) || (goingUp && atTop))) return;
    event.preventDefault();
    pdfTurning = true;
    turnPdfPage(goingDown ? 1 : -1)
      .catch((err) => setStatus("fallback", err.message))
      .finally(() => { pdfTurning = false; });
  }, { passive: false });
  $("pdfZoomIn").addEventListener("click", () => {
    state.pdfFitMode = "manual";
    state.pdfScale = Math.min(2.5, state.pdfScale + 0.2);
    renderPdfPage(state.pdfPage).catch((err) => setStatus("fallback", err.message));
  });
  $("pdfZoomOut").addEventListener("click", () => {
    state.pdfFitMode = "manual";
    state.pdfScale = Math.max(0.35, state.pdfScale - 0.2);
    renderPdfPage(state.pdfPage).catch((err) => setStatus("fallback", err.message));
  });
  $("pdfFit").addEventListener("click", () => {
    state.pdfFitMode = "page";
    renderPdfPage(state.pdfPage).catch((err) => setStatus("fallback", err.message));
  });
  $("pdfFitWidth").addEventListener("click", () => {
    state.pdfFitMode = "width";
    renderPdfPage(state.pdfPage).catch((err) => setStatus("fallback", err.message));
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (state.mode !== "pdf" || !state.pdfDoc || state.pdfFitMode === "manual") return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      renderPdfPage(state.pdfPage).catch(() => {});
    }, 150);
  });

  if (state.docId) {
    loadDoc();
  } else {
    $("backReview").href = `/review?symbol=${encodeURIComponent(symbol)}`;
    docsPromise.then(renderPicker).catch((err) => {
      $("docPicker").textContent = `财报清单加载失败：${err.message}`;
    });
    $("pickerPanel").hidden = false;
    renderDocNav();
  }
})();
