/* 财报体检独立页：保留原有量化摘要，主体交给共享的 forensics.js 渲染。 */
(function () {
  const symbol = new URLSearchParams(window.location.search).get("symbol") || "600519.SH";
  const { getJSON } = window.VC;

  function el(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text ?? "";
    return node;
  }

  // K 线与财报体检两条独立异步链路，都就绪后由 applyForensicMarkers 合流
  let klineChart = null;
  let klineBars = [];
  let forensicData = null;

  function markerTooltip(params) {
    const arr = Array.isArray(params) ? params : [params];
    const esc = window.VC.escapeHtml;
    const lines = [];
    const candle = arr.find((p) => p.seriesType === "candlestick");
    if (candle) {
      lines.push('<div class="kl-tt-date">' + esc(candle.axisValue || candle.name || "") + "</div>");
      const raw = (candle.data && candle.data.value) ? candle.data.value : (candle.data || candle.value);
      if (Array.isArray(raw)) {
        const nums = raw.filter((x) => typeof x === "number");
        if (nums.length >= 4) {
          const [o, c, l, h] = nums.slice(-4);
          lines.push('<div class="kl-tt-ohlc">开 ' + o + "　收 " + c + "　低 " + l + "　高 " + h + "</div>");
        }
      }
    }
    const events = arr.filter((p) => p.seriesType === "scatter" && p.data && p.data.meta);
    for (const ev of events) {
      const m = ev.data.meta;
      lines.push(
        '<div class="kl-tt-ev" style="border-left:3px solid ' + m.color + '">'
        + '<div class="kl-tt-ev-head">' + esc(m.title)
        + ' <span class="kl-tt-tone">' + esc(m.toneLabel) + "</span></div>"
        + '<div class="kl-tt-meta">' + esc(m.period) + " · " + esc(m.severity)
        + " · " + esc(m.judgement) + "</div>"
        + (m.detail ? '<div class="kl-tt-detail">' + esc(m.detail) + "</div>" : "")
        + '<div class="kl-tt-hint">' + esc(m.hint || "点击圆点定位到上方对应卡片") + "</div>"
        + "</div>"
      );
    }
    return lines.join("") || "";
  }

  function renderMarkerLegend(result, total) {
    const box = document.getElementById("klineMarkerLegend");
    if (!box) return;
    box.innerHTML = "";
    if (!total) {
      const empty = el("span", "财报体检加载后，将按报告期在此标注关键点");
      empty.className = "kl-legend-empty";
      box.appendChild(empty);
      return;
    }
    const tones = window.VC.MARKER_TONES || {};
    for (const key of Object.keys(tones)) {
      const info = tones[key];
      const count = (result.counts && result.counts[key]) || 0;
      const item = el("span", "kl-legend-item");
      const dot = el("span", "kl-legend-dot");
      if (info) dot.style.background = info.color;
      if (info && info.symbol === "diamond") dot.classList.add("is-diamond");
      item.appendChild(dot);
      item.appendChild(el("span", null, (info ? info.label : key) + " " + count));
      box.appendChild(item);
    }
    box.appendChild(
      el("span", "kl-legend-hint", `共 ${total} 个财报关键点（含原文通读线索）· 按报告期定位 · 点击圆点跳转到上方对应卡片`)
    );
  }

  function applyForensicMarkers() {
    if (!klineChart) return;
    const result = window.VC.buildForensicMarkers
      ? window.VC.buildForensicMarkers(forensicData, klineBars)
      : { points: [], counts: {}, earliest: null };
    const points = result.points || [];
    klineChart.setOption({ series: [{}, { data: points }] });
    renderMarkerLegend(result, points.length);
    if (points.length && result.earliest && klineBars.length) {
      const idx = klineBars.findIndex((b) => b.trade_date >= result.earliest);
      if (idx > 0) {
        const start = Math.max(0, Math.min(95, (idx / klineBars.length) * 100 - 4));
        klineChart.setOption({
          dataZoom: [{ start: start, end: 100 }, { start: start, end: 100 }],
        });
      }
    }
  }

  function renderKline(kline) {
    klineBars = kline || [];
    if (!klineChart) {
      klineChart = echarts.init(document.getElementById("kline"));
      klineChart.on("click", function (params) {
        if (params.seriesType !== "scatter" || !params.data) return;
        if (params.data.anchorId && window.VC.focusForensicAnchor) {
          window.VC.focusForensicAnchor(params.data.anchorId);
        } else if (params.data.signalId) {
          window.VC.focusForensicSignal(params.data.signalId);
        }
      });
      window.addEventListener("resize", function () {
        if (klineChart) klineChart.resize();
      });
    }
    klineChart.setOption({
      animation: false,
      grid: { left: 56, right: 20, top: 20, bottom: 60 },
      xAxis: { type: "category", data: klineBars.map((b) => b.trade_date) },
      yAxis: [
        { scale: true },
        { min: 0, max: 1, show: false, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20, bottom: 8 }],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        formatter: markerTooltip,
      },
      series: [
        {
          name: "日K",
          type: "candlestick",
          yAxisIndex: 0,
          data: klineBars.map((b) => [b.open, b.close, b.low, b.high]),
        },
        {
          name: "财报关键点",
          type: "scatter",
          xAxisIndex: 0,
          yAxisIndex: 1,
          data: [],
        },
      ],
    }, true);
    applyForensicMarkers();
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
    const onForensics = (data) => {
      if (!data) return;
      forensicData = data;
      applyForensicMarkers();
    };
    window.VC.loadForensics(box, symbol).then(onForensics);
    const refreshBtn = document.getElementById("forensicsRefresh");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        refreshBtn.disabled = true;
        window.VC.loadForensics(box, symbol, { refresh: true })
          .then(onForensics)
          .finally(() => {
            refreshBtn.disabled = false;
          });
      });
    }
  }

  getJSON(`/api/stock/${symbol}/kline`)
    .then(renderKline)
    .catch(console.error);

  renderBasics().catch(console.error);
})();
