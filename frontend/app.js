(() => {
  "use strict";

  // ---------------------------------------------------------------------------
  // Small DOM + formatting helpers
  // ---------------------------------------------------------------------------
  const byId = (id) => document.getElementById(id);
  const isNum = (value) => value != null && Number.isFinite(Number(value));
  const num = (value) => Number(value);
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const number = (value) => (!isNum(value) ? "—" : num(value).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
  const percent = (value) => (!isNum(value) ? "—" : `${(num(value) * 100).toFixed(2)}%`);
  const money = (value) => (typeof value === "string" ? value : `₹ ${number(value)}`);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[c]));

  // ---------------------------------------------------------------------------
  // Session state
  // ---------------------------------------------------------------------------
  let result = null;
  let greek = "delta";
  let quoteSeries = null;
  let quoteRising = true;
  let suggestionTimer;
  let analysisTimer;
  let suggestionRequest;
  let analyticsRequest;
  let quoteRequest;
  let analyticsRevision = 0;
  let quoteRevision = 0;

  // ---------------------------------------------------------------------------
  // Black–Scholes pricing + Greeks (client-side, so every chart recomputes from
  // the live inputs and can never get "stuck" on a stale server response).
  // ---------------------------------------------------------------------------
  function erf(x) {
    const sign = x < 0 ? -1 : 1;
    const ax = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * ax);
    const poly = (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t);
    return sign * (1 - poly * Math.exp(-ax * ax));
  }
  const normCdf = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
  const normPdf = (x) => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);

  // Full analytic price + Greeks for a single European option. Returns null when
  // inputs are degenerate so callers can skip the point instead of drawing NaN.
  function bsGreeks(spot, strike, rate, maturity, vol, type) {
    if (!(spot > 0 && strike > 0 && vol > 0 && maturity > 0)) return null;
    const sqrtT = Math.sqrt(maturity);
    const d1 = (Math.log(spot / strike) + (rate + 0.5 * vol * vol) * maturity) / (vol * sqrtT);
    const d2 = d1 - vol * sqrtT;
    const disc = Math.exp(-rate * maturity);
    const nd1 = normCdf(d1);
    const nd2 = normCdf(d2);
    const pdf = normPdf(d1);
    const call = type !== "put";
    const price = call ? spot * nd1 - strike * disc * nd2 : strike * disc * (1 - nd2) - spot * (1 - nd1);
    const delta = call ? nd1 : nd1 - 1;
    const gamma = pdf / (spot * vol * sqrtT);
    const vega = spot * pdf * sqrtT;
    const theta = call
      ? -(spot * pdf * vol) / (2 * sqrtT) - rate * strike * disc * nd2
      : -(spot * pdf * vol) / (2 * sqrtT) + rate * strike * disc * (1 - nd2);
    const rho = call ? strike * maturity * disc * nd2 : -strike * maturity * disc * (1 - nd2);
    return { price, delta, gamma, vega, theta, rho };
  }

  // Aggregate a metric ("price", "delta", …) across every leg of a position.
  function positionMetric(legs, metric, spot, refStrike, rate, maturity, vol) {
    if (!Array.isArray(legs)) return NaN;
    let total = 0;
    for (const leg of legs) {
      const qty = num(leg.quantity);
      if (!Number.isFinite(qty)) continue;
      if (leg.kind === "stock") {
        total += qty * (metric === "price" ? spot : metric === "delta" ? 1 : 0);
        continue;
      }
      const strike = isNum(leg.strike) ? num(leg.strike) : refStrike;
      const g = bsGreeks(spot, strike, rate, maturity, vol, leg.type);
      if (!g || !Number.isFinite(g[metric])) return NaN;
      total += qty * g[metric];
    }
    return total;
  }

  // Intrinsic value at expiry (used for the payoff curve fallback).
  function expiryValue(legs, terminal, refStrike) {
    if (!Array.isArray(legs)) return NaN;
    let value = 0;
    for (const leg of legs) {
      const qty = num(leg.quantity);
      if (!Number.isFinite(qty)) continue;
      if (leg.kind === "stock") { value += qty * terminal; continue; }
      const strike = isNum(leg.strike) ? num(leg.strike) : refStrike;
      value += qty * (leg.type === "put" ? Math.max(strike - terminal, 0) : Math.max(terminal - strike, 0));
    }
    return value;
  }

  // ---------------------------------------------------------------------------
  // Robust canvas line-chart engine. One entry point, drawLineChart(id, config),
  // which is wrapped so a bad data point can never break the page: it validates
  // everything, filters non-finite values, and paints a placeholder when empty.
  // Charts redraw on container resize and support a hover crosshair + tooltip.
  // ---------------------------------------------------------------------------
  const chartState = new Map(); // canvasId -> { config, geometry }

  const PALETTE = { grid: "#22344c", text: "#8fa2bc", bg: "#0b1728", zero: "#6c7f9a", crosshair: "#41597d" };

  function ensureTooltip(canvas) {
    let tip = canvas._tooltip;
    if (tip) return tip;
    const parent = canvas.parentElement;
    if (parent && getComputedStyle(parent).position === "static") parent.style.position = "relative";
    tip = document.createElement("div");
    tip.style.cssText = "position:absolute;pointer-events:none;z-index:6;padding:6px 9px;border-radius:8px;" +
      "background:rgba(10,20,34,.94);border:1px solid #2c445f;color:#e6eefb;font-size:11px;" +
      "line-height:1.45;white-space:nowrap;transform:translate(-50%,-115%);opacity:0;transition:opacity .08s;" +
      "box-shadow:0 10px 26px rgba(2,8,18,.55)";
    (parent || document.body).appendChild(tip);
    canvas._tooltip = tip;
    return tip;
  }

  function sizeCanvas(canvas) {
    const cssWidth = Math.max(canvas.clientWidth || canvas.parentElement?.clientWidth || 0, 280);
    const cssHeight = Number(canvas.getAttribute("height")) || 220;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    canvas.style.width = "100%";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width: cssWidth, height: cssHeight };
  }

  const PADL = 54, PADR = 16, PADT = 16, PADB = 30;

  function finitePoints(series) {
    return (series || []).map((s) => ({
      ...s,
      points: (s.points || []).filter((p) => isNum(p.x) && isNum(p.y)).map((p) => ({ x: num(p.x), y: num(p.y) })),
    }));
  }

  function paint(canvas, config) {
    const { ctx, width, height } = sizeCanvas(canvas);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = PALETTE.bg;
    ctx.fillRect(0, 0, width, height);

    const series = finitePoints(config.series).filter((s) => s.points.length);
    const allPoints = series.flatMap((s) => s.points);
    if (!allPoints.length) {
      ctx.fillStyle = PALETTE.text;
      ctx.font = "12px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(config.placeholder || "No data available", width / 2, height / 2);
      chartState.set(canvas.id, null);
      return;
    }

    let xMin = Math.min(...allPoints.map((p) => p.x));
    let xMax = Math.max(...allPoints.map((p) => p.x));
    const extraY = [...(config.hlines || []).map((h) => h.y), ...(config.includeZero ? [0] : [])].filter(isNum);
    let yMin = Math.min(...allPoints.map((p) => p.y), ...extraY);
    let yMax = Math.max(...allPoints.map((p) => p.y), ...extraY);
    if (xMax - xMin < 1e-9) { xMax += 1; xMin -= 1; }
    const yPad = (yMax - yMin) * 0.08 || Math.abs(yMax) * 0.1 || 1;
    yMin -= yPad; yMax += yPad;

    const plotW = width - PADL - PADR, plotH = height - PADT - PADB;
    const X = (x) => PADL + ((x - xMin) / (xMax - xMin)) * plotW;
    const Y = (y) => PADT + ((yMax - y) / (yMax - yMin)) * plotH;

    // Gridlines + y-axis tick labels
    ctx.font = "10px system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i += 1) {
      const gy = PADT + (i / 4) * plotH;
      const value = yMax - (i / 4) * (yMax - yMin);
      ctx.strokeStyle = PALETTE.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PADL, gy); ctx.lineTo(width - PADR, gy); ctx.stroke();
      ctx.fillStyle = PALETTE.text; ctx.textAlign = "right";
      ctx.fillText((config.yFormat || number)(value), PADL - 6, gy);
    }
    // x-axis tick labels
    ctx.textBaseline = "top"; ctx.textAlign = "center";
    for (let i = 0; i <= 4; i += 1) {
      const value = xMin + (i / 4) * (xMax - xMin);
      const gx = clamp(X(value), PADL + 12, width - PADR - 12);
      ctx.fillStyle = PALETTE.text;
      ctx.fillText((config.xFormat || number)(value), gx, height - PADB + 8);
    }

    drawSeriesAndMarkers(ctx, config, series, { X, Y, xMin, xMax, yMin, yMax, plotH });
    chartState.set(canvas.id, { config, series, geom: { X, Y, xMin, xMax, yMin, yMax, width, height, plotH } });
  }

  function hexToRgba(hex, alpha) {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || "");
    if (!m) return `rgba(115,167,255,${alpha})`;
    return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${alpha})`;
  }

  function drawSeriesAndMarkers(ctx, config, series, g) {
    const { X, Y, xMin, xMax, yMin, yMax } = g;
    const clampY = (y) => clamp(Y(y), PADT, PADT + g.plotH);
    // Horizontal reference lines (e.g. zero P&L, premium paid)
    for (const h of config.hlines || []) {
      if (!isNum(h.y) || h.y < yMin || h.y > yMax) continue;
      ctx.strokeStyle = h.color || PALETTE.zero; ctx.lineWidth = 1;
      ctx.setLineDash(h.dash || [4, 4]);
      ctx.beginPath(); ctx.moveTo(X(xMin), Y(h.y)); ctx.lineTo(X(xMax), Y(h.y)); ctx.stroke();
      ctx.setLineDash([]);
    }
    // Series (optional area fill to the zero baseline, then the line)
    const baseY = clamp(Y(0), PADT, PADT + g.plotH);
    for (const s of series) {
      if (s.fill) {
        ctx.beginPath();
        s.points.forEach((p, i) => (i ? ctx.lineTo(X(p.x), clampY(p.y)) : ctx.moveTo(X(p.x), clampY(p.y))));
        ctx.lineTo(X(s.points[s.points.length - 1].x), baseY);
        ctx.lineTo(X(s.points[0].x), baseY);
        ctx.closePath();
        ctx.fillStyle = hexToRgba(s.color, 0.14);
        ctx.fill();
      }
      ctx.strokeStyle = s.color || "#73a7ff";
      ctx.lineWidth = s.width || 2.4;
      ctx.beginPath();
      s.points.forEach((p, i) => (i ? ctx.lineTo(X(p.x), clampY(p.y)) : ctx.moveTo(X(p.x), clampY(p.y))));
      ctx.stroke();
    }
    // Vertical markers (current spot, break-evens, current vol …)
    for (const v of config.vlines || []) {
      if (!isNum(v.x) || v.x < xMin || v.x > xMax) continue;
      ctx.strokeStyle = v.color || "#f0b46b"; ctx.lineWidth = 1;
      ctx.setLineDash(v.dash || [5, 4]);
      ctx.beginPath(); ctx.moveTo(X(v.x), PADT); ctx.lineTo(X(v.x), PADT + g.plotH); ctx.stroke();
      ctx.setLineDash([]);
      if (v.label) {
        ctx.fillStyle = v.color || "#f0b46b"; ctx.font = "9px system-ui, sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "top";
        ctx.fillText(v.label, clamp(X(v.x), PADL + 16, g.width - PADR - 16), PADT + 2);
      }
    }
  }

  // Public entry point. Everything is wrapped so one bad point can never throw
  // and kill the page — on any failure we simply paint the placeholder.
  const resizeObserved = new WeakSet();
  function drawLineChart(id, config) {
    const canvas = byId(id);
    if (!canvas) return;
    canvas._chartConfig = config;
    try {
      paint(canvas, config);
    } catch (err) {
      console.error(`chart ${id} failed`, err);
      try {
        const { ctx, width, height } = sizeCanvas(canvas);
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = PALETTE.bg; ctx.fillRect(0, 0, width, height);
        ctx.fillStyle = PALETTE.text; ctx.font = "12px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(config.placeholder || "Chart unavailable", width / 2, height / 2);
      } catch { /* nothing more we can do */ }
    }
    wireInteractions(canvas);
  }

  function repaint(canvas) {
    const config = canvas._chartConfig;
    if (config) { try { paint(canvas, config); } catch { /* ignore */ } }
  }

  function wireInteractions(canvas) {
    if (resizeObserved.has(canvas)) return;
    resizeObserved.add(canvas);
    if (typeof ResizeObserver !== "undefined") {
      let frame;
      const ro = new ResizeObserver(() => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => repaint(canvas));
      });
      ro.observe(canvas.parentElement || canvas);
    }
    canvas.addEventListener("mousemove", (evt) => showCrosshair(canvas, evt));
    canvas.addEventListener("mouseleave", () => {
      repaint(canvas);
      const tip = canvas._tooltip;
      if (tip) tip.style.opacity = "0";
    });
  }

  // Hover crosshair + tooltip. Repaints the chart, then overlays a vertical line
  // and a dot on every series at the x nearest the cursor.
  function showCrosshair(canvas, evt) {
    const state = chartState.get(canvas.id);
    if (!state) return;
    repaint(canvas);
    const fresh = chartState.get(canvas.id);
    if (!fresh) return;
    const { config, series, geom } = fresh;
    const { X, Y, xMin, xMax } = geom;
    const rect = canvas.getBoundingClientRect();
    const px = evt.clientX - rect.left;
    const dataX = clamp(xMin + ((px - PADL) / (geom.width - PADL - PADR)) * (xMax - xMin), xMin, xMax);
    const primary = series[0];
    if (!primary || !primary.points.length) return;
    let idx = 0, best = Infinity;
    primary.points.forEach((p, i) => {
      const d = Math.abs(p.x - dataX);
      if (d < best) { best = d; idx = i; }
    });
    const anchorX = primary.points[idx].x;
    const ctx = canvas.getContext("2d");
    ctx.strokeStyle = PALETTE.crosshair; ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(X(anchorX), PADT); ctx.lineTo(X(anchorX), PADT + geom.plotH); ctx.stroke();
    ctx.setLineDash([]);
    const rows = [];
    for (const s of series) {
      const pt = s.points[idx] && Math.abs(s.points[idx].x - anchorX) < 1e-6
        ? s.points[idx]
        : s.points.find((p) => Math.abs(p.x - anchorX) < 1e-6);
      if (!pt) continue;
      const cy = clamp(Y(pt.y), PADT, PADT + geom.plotH);
      ctx.fillStyle = s.color || "#73a7ff";
      ctx.beginPath(); ctx.arc(X(anchorX), cy, 3.5, 0, Math.PI * 2); ctx.fill();
      rows.push(`<span style="color:${s.color || "#73a7ff"}">●</span> ${escapeHtml(s.label || "")} <b>${(config.tipFormat || config.yFormat || number)(pt.y)}</b>`);
    }
    const tip = ensureTooltip(canvas);
    tip.innerHTML = `<div style="color:${PALETTE.text};margin-bottom:2px">${(config.xFormat || number)(anchorX)}</div>${rows.join("<br>")}`;
    tip.style.left = `${clamp(X(anchorX), 60, geom.width - 60)}px`;
    tip.style.top = `${PADT + 6}px`;
    tip.style.opacity = "1";
  }

  // ---------------------------------------------------------------------------
  // Strategy model (mirrors the backend strategy_legs so every chart can be
  // computed locally and stays live even before the server responds).
  // ---------------------------------------------------------------------------
  const STRATEGY_TITLES = {
    long_call: "Long call payoff", long_put: "Long put payoff",
    short_call: "Short call payoff", short_put: "Short put payoff",
    straddle: "ATM straddle payoff", collar: "Collar payoff",
  };

  function buildLegs(strike, strategy) {
    switch (strategy) {
      case "long_put": return [{ kind: "option", type: "put", strike, quantity: 1 }];
      case "short_call": return [{ kind: "option", type: "call", strike, quantity: -1 }];
      case "short_put": return [{ kind: "option", type: "put", strike, quantity: -1 }];
      case "straddle": return [
        { kind: "option", type: "call", strike, quantity: 1 },
        { kind: "option", type: "put", strike, quantity: 1 },
      ];
      case "collar": return [
        { kind: "stock", quantity: 1 },
        { kind: "option", type: "put", strike: strike * 0.95, quantity: 1 },
        { kind: "option", type: "call", strike: strike * 1.05, quantity: -1 },
      ];
      default: return [{ kind: "option", type: "call", strike, quantity: 1 }];
    }
  }

  function readInputs() {
    const spot = clamp(num(byId("spot").value) || 100, 1, 1e7);
    const strike = clamp(num(byId("strike").value) || 100, 1, 1e7);
    const vol = clamp(num(byId("vol").value) || 0.25, 0.01, 10);
    const rate = clamp(num(byId("rate").value) || 0, 0, 1);
    const maturity = clamp(num(byId("maturity").value) || 1, 0.01, 30);
    const type = byId("type").value === "put" ? "put" : "call";
    const strategy = byId("strategy").value || "long_call";
    return { spot, strike, vol, rate, maturity, type, strategy };
  }

  const setText = (id, value) => { const el = byId(id); if (el) el.textContent = value; };

  // ---- Strategy payoff (P&L at expiry) --------------------------------------
  function metricsFor(strategy, strike, cost) {
    const pk = strike * 0.95, ck = strike * 1.05;
    switch (strategy) {
      case "long_put": return { maxLoss: cost, be: [strike - cost], maxGain: strike - cost };
      case "short_call": return { maxLoss: "Unlimited", be: [strike - cost], maxGain: -cost };
      case "short_put": return { maxLoss: strike + cost, be: [strike + cost], maxGain: -cost };
      case "straddle": return { maxLoss: cost, be: [strike - cost, strike + cost], maxGain: "Unlimited" };
      case "collar": return { maxLoss: Math.max(cost - pk, 0), be: [cost], maxGain: Math.max(ck - cost, 0) };
      default: return { maxLoss: cost, be: [strike + cost], maxGain: "Unlimited" };
    }
  }

  function renderPayoffChart(inp) {
    const { spot, strike, rate, maturity, vol, strategy } = inp;
    const legs = buildLegs(strike, strategy);
    const cost = positionMetric(legs, "price", spot, strike, rate, maturity, vol);
    const lo = Math.max(1, spot * 0.5), hi = spot * 1.5;
    const points = [];
    for (let i = 0; i <= 80; i += 1) {
      const terminal = lo + (i / 80) * (hi - lo);
      const pnl = expiryValue(legs, terminal, strike) - cost;
      if (isNum(pnl)) points.push({ x: terminal, y: pnl });
    }
    const m = metricsFor(strategy, strike, cost);
    const breakEvens = (m.be || []).filter((b) => isNum(b) && b >= lo && b <= hi);
    drawLineChart("payoff-chart", {
      series: [{ points, color: "#48d4a0", fill: true, label: "P&L", width: 2.6 }],
      hlines: [{ y: 0, color: "#6c7f9a", dash: [5, 4] }],
      vlines: [
        { x: spot, color: "#f0b46b", label: "Spot", dash: [4, 4] },
        ...breakEvens.map((b) => ({ x: b, color: "#b28cff", label: "B/E", dash: [3, 4] })),
      ],
      includeZero: true, yFormat: (v) => `₹${number(v)}`, xFormat: (v) => number(v),
      tipFormat: (v) => `₹${number(v)}`, placeholder: "Adjust inputs to plot P&L",
    });
    setText("strategy-title", STRATEGY_TITLES[strategy] || "Strategy payoff");
    const box = byId("payoff-metrics");
    if (box) {
      const gain = m.maxGain === "Unlimited" ? "Unlimited" : `₹${number(Math.abs(num(m.maxGain)))}`;
      const loss = m.maxLoss === "Unlimited" ? "Unlimited" : `₹${number(Math.abs(num(m.maxLoss)))}`;
      const be = breakEvens.length ? breakEvens.map((b) => number(b)).join(" / ") : "—";
      box.innerHTML = `<span>Max loss <b>${loss}</b></span><span>Break-even <b>${be}</b></span><span>Max gain <b>${gain}</b></span>`;
    }
  }

  // ---- Volatility vs price ---------------------------------------------------
  // Position value swept across implied volatility, with markers at the current
  // IV (purple) and realized vol (green) so the card legend stays meaningful.
  function renderVolPriceChart(inp, data) {
    const { spot, strike, rate, maturity, vol, strategy } = inp;
    const legs = buildLegs(strike, strategy);
    const points = [];
    for (let i = 0; i <= 80; i += 1) {
      const v = 0.02 + (i / 80) * (1.2 - 0.02);
      const price = positionMetric(legs, "price", spot, strike, rate, maturity, v);
      if (isNum(price)) points.push({ x: v, y: price });
    }
    const realized = num(data?.volatility?.realizedVol);
    const vlines = [{ x: vol, color: "#b28cff", label: "IV", dash: [4, 4] }];
    if (isNum(realized) && realized > 0 && realized <= 1.2) {
      vlines.push({ x: realized, color: "#48d4a0", label: "RV", dash: [4, 4] });
    }
    drawLineChart("vol-chart", {
      series: [{ points, color: "#76aaff", fill: true, label: "Position value" }],
      vlines, yFormat: (v) => `₹${number(v)}`, xFormat: (v) => percent(v),
      tipFormat: (v) => `₹${number(v)}`, placeholder: "Adjust inputs to plot value vs vol",
    });
    if (data?.volatility) {
      const vsum = data.volatility;
      setText("vol-regime", vsum.regime || "—");
      const info = byId("vol-info");
      if (info) {
        info.innerHTML =
          `<span>Garman–Klass <b>${percent(vsum.gkVol)}</b></span>` +
          `<span>30d forecast <b>${percent(vsum.forward30d)}</b></span>` +
          `<span>IV / RV <b>${isNum(vsum.ivVsRealized) ? num(vsum.ivVsRealized).toFixed(2) : "—"}</b></span>`;
      }
    }
  }

  // ---- Greeks / price vs spot ------------------------------------------------
  const GREEK_META = {
    price: { label: "Price", sym: "Price" }, delta: { label: "Delta", sym: "Δ" },
    gamma: { label: "Gamma", sym: "Γ" }, vega: { label: "Vega", sym: "Vega" },
    theta: { label: "Theta", sym: "Θ" }, rho: { label: "Rho", sym: "ρ" },
  };

  function renderGreekChart(inp) {
    const { spot, strike, rate, maturity, vol, type } = inp;
    const metric = GREEK_META[greek] ? greek : "delta";
    const lo = Math.max(1, spot * 0.5), hi = spot * 1.5;
    const points = [];
    for (let i = 0; i <= 80; i += 1) {
      const s = lo + (i / 80) * (hi - lo);
      const g = bsGreeks(s, strike, rate, maturity, vol, type);
      if (g && isNum(g[metric])) points.push({ x: s, y: g[metric] });
    }
    const meta = GREEK_META[metric];
    drawLineChart("greek-chart", {
      series: [{ points, color: "#76aaff", fill: false, label: meta.label, width: 2.6 }],
      hlines: metric === "price" ? [] : [{ y: 0, color: "#6c7f9a", dash: [4, 4] }],
      vlines: [
        { x: spot, color: "#f0b46b", label: "Spot", dash: [4, 4] },
        { x: strike, color: "#b28cff", label: "K", dash: [3, 4] },
      ],
      includeZero: metric !== "price",
      yFormat: metric === "price" ? (v) => `₹${number(v)}` : number,
      xFormat: (v) => number(v), tipFormat: metric === "price" ? (v) => `₹${number(v)}` : number,
      placeholder: "Adjust inputs to plot sensitivity",
    });
    setText("greek-chart-caption", `${meta.label} response as the underlying spot changes`);
    setText("surface-title", `${meta.label} surface`);
    setText("surface-metric", meta.sym);
    const g = bsGreeks(spot, strike, rate, maturity, vol, type);
    const cards = byId("greek-cards");
    if (cards && g) {
      cards.innerHTML = ["delta", "gamma", "vega", "theta", "rho"].map((k) =>
        `<article><span>${GREEK_META[k].label}</span><strong>${number(g[k])}</strong></article>`).join("");
    }
  }

  // ---- Greeks surface table --------------------------------------------------
  function renderSurface(data) {
    const tbody = document.querySelector("#surface tbody");
    if (!tbody) return;
    const rows = Array.isArray(data?.surface) ? data.surface : [];
    const metric = GREEK_META[greek] && greek !== "price" ? greek : "delta";
    const maxAbs = Math.max(1e-9, ...rows.map((r) => Math.abs(num(r[metric])) || 0));
    tbody.innerHTML = rows.map((r) => {
      const heat = clamp(Math.abs(num(r[metric])) / maxAbs, 0, 1);
      return `<tr><td>${escapeHtml(r.dte)}d</td><td>${number(r.strike)}</td>` +
        `<td style="--v:${heat.toFixed(3)}">${number(r[metric])}</td>` +
        `<td>${number(r.gamma)}</td><td>${number(r.vega)}</td></tr>`;
    }).join("") || `<tr><td colspan="5" style="color:var(--muted)">Run analysis to build the surface</td></tr>`;
  }

  // ---- Underlying quote chart ------------------------------------------------
  function renderQuoteChart() {
    const bars = Array.isArray(quoteSeries) ? quoteSeries : [];
    const points = bars
      .map((b, i) => ({ x: isNum(b.ts) ? num(b.ts) : i, y: num(b.close) }))
      .filter((p) => isNum(p.y));
    const color = quoteRising ? "#48d4a0" : "#fb8c9b";
    const fmtTime = (t) => {
      if (!isNum(t) || t < 1e6) return number(t);
      const d = new Date(num(t) * 1000);
      return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    };
    drawLineChart("quote-chart", {
      series: [{ points, color, fill: true, label: "Close", width: 2.4 }],
      yFormat: (v) => `₹${number(v)}`, xFormat: fmtTime, tipFormat: (v) => `₹${number(v)}`,
      placeholder: "Fetch a quote to plot the intraday close",
    });
  }

  function renderCharts(inp, data) {
    renderPayoffChart(inp);
    renderVolPriceChart(inp, data);
    renderGreekChart(inp);
    renderSurface(data);
  }

  // ---------------------------------------------------------------------------
  // Populate the numeric cards from the analytics response.
  // ---------------------------------------------------------------------------
  function render(data) {
    if (!data) return;
    result = data;
    setText("m-price", money(data.modelPrice));
    const iv = data.impliedVol || {};
    setText("m-iv", percent(iv.estimatedIv));
    setText("m-signal", iv.signal === "rich" ? "Estimated IV rich vs model"
      : iv.signal === "cheap" ? "Estimated IV cheap vs model" : "Awaiting analysis");
    const vsum = data.volatility || {};
    setText("m-rv", percent(vsum.realizedVol));
    const ms = data.latency?.analyticsMs ?? data.requestMs;
    setText("m-latency", isNum(ms) ? `${num(ms).toFixed(1)} ms` : "—");
    setText("request-time", isNum(data.requestMs) ? `${num(data.requestMs).toFixed(1)} ms` : "—");
    const models = data.models || {};
    const rows = document.querySelectorAll("#models .model-row strong");
    if (rows[0]) rows[0].textContent = money(models.blackScholes);
    if (rows[1]) rows[1].textContent = money(models.binomialAmerican);
    if (rows[2]) rows[2].textContent = money(models.monteCarloAntithetic);
    const spread = [models.blackScholes, models.binomialAmerican, models.monteCarloAntithetic].filter(isNum);
    const note = byId("model-note");
    if (note && spread.length) {
      const divergence = Math.max(...spread) - Math.min(...spread);
      note.textContent = `Max model divergence ${money(round4(divergence))} across the three methods.`;
    }
  }

  const round4 = (v) => Math.round(num(v) * 10000) / 10000;

  // ---------------------------------------------------------------------------
  // Risk / scenario panel: reprice the live position under spot & IV shocks.
  // ---------------------------------------------------------------------------
  function updateScenario() {
    const inp = readInputs();
    const spotShock = num(byId("spot-shock").value) / 100;
    const ivShock = num(byId("iv-shock").value) / 100;
    setText("spot-shock-value", `${(spotShock * 100).toFixed(0)}%`);
    setText("iv-shock-value", `${(ivShock * 100).toFixed(0)}%`);
    const legs = buildLegs(inp.strike, inp.strategy);
    const shockedSpot = inp.spot * (1 + spotShock);
    const shockedVol = clamp(inp.vol * (1 + ivShock), 0.001, 10);
    const base = positionMetric(legs, "price", inp.spot, inp.strike, inp.rate, inp.maturity, inp.vol);
    const shocked = positionMetric(legs, "price", shockedSpot, inp.strike, inp.rate, inp.maturity, shockedVol);
    const pnl = shocked - base;
    const el = byId("scenario-pnl");
    if (el) {
      el.textContent = isNum(pnl) ? `${pnl >= 0 ? "+" : "−"}₹ ${number(Math.abs(pnl))}` : "—";
      el.classList.toggle("gain", isNum(pnl) && pnl >= 0);
      el.classList.toggle("loss", isNum(pnl) && pnl < 0);
    }
    const vega = positionMetric(legs, "vega", inp.spot, inp.strike, inp.rate, inp.maturity, inp.vol);
    setText("vega-limit", isNum(vega) ? number(vega) : "—");
  }

  // ---------------------------------------------------------------------------
  // Networking. Every fetch is abortable and revision-guarded so a slow response
  // can never clobber a newer one, and a failure never blanks the charts (they
  // are already drawn from the local inputs).
  // ---------------------------------------------------------------------------
  async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function runAnalysis() {
    const inp = readInputs();
    // Draw immediately from local math — the UI is never blocked on the server.
    renderCharts(inp, result);
    updateScenario();
    if (analyticsRequest) analyticsRequest.abort();
    analyticsRequest = new AbortController();
    const revision = (analyticsRevision += 1);
    setText("request-time", "…");
    try {
      const data = await fetchJSON("/api/analytics", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          spot: inp.spot, strike: inp.strike, rate: inp.rate, maturity: inp.maturity,
          vol: inp.vol, option_type: inp.type, strategy: inp.strategy,
          series: Array.isArray(quoteSeries) ? quoteSeries.slice(-260) : null,
        }),
        signal: analyticsRequest.signal,
      });
      if (revision !== analyticsRevision) return; // a newer request already ran
      render(data);
      renderCharts(inp, data);
      updateScenario();
    } catch (err) {
      if (err.name === "AbortError") return;
      const note = byId("model-note");
      if (note) note.textContent = "Live model comparison unavailable — charts use the in-browser model.";
    }
  }

  async function loadQuote() {
    const symbol = (byId("symbol").value || "").trim().toUpperCase();
    if (!symbol) return;
    if (quoteRequest) quoteRequest.abort();
    quoteRequest = new AbortController();
    const revision = (quoteRevision += 1);
    const status = byId("quote-status");
    if (status) status.innerHTML = "<i></i> Fetching…";
    try {
      const data = await fetchJSON(`/api/stock?symbol=${encodeURIComponent(symbol)}&market=equity`,
        { signal: quoteRequest.signal });
      if (revision !== quoteRevision) return;
      const q = data.quote || {};
      quoteSeries = Array.isArray(q.series) ? q.series : [];
      const change = num(q.change);
      quoteRising = !isNum(change) || change >= 0;
      setText("quote-symbol", q.symbol || symbol);
      setText("quote-last", money(q.lastPrice));
      const changeEl = byId("quote-change");
      if (changeEl) {
        const pct = isNum(q.prevClose) && q.prevClose ? (change / q.prevClose) * 100 : NaN;
        changeEl.textContent = isNum(change)
          ? `${change >= 0 ? "▲" : "▼"} ${number(Math.abs(change))}${isNum(pct) ? ` (${pct.toFixed(2)}%)` : ""}`
          : "—";
        changeEl.style.color = quoteRising ? "var(--green)" : "#fb8c9b";
      }
      const stats = byId("quote-stats");
      if (stats) stats.innerHTML =
        `<span>Open <b>${number(q.openPrice)}</b></span><span>High <b>${number(q.highPrice)}</b></span>` +
        `<span>Low <b>${number(q.lowPrice)}</b></span>`;
      if (status) status.innerHTML = "<i></i> Provider quote";
      setText("quote-source", `${q.symbol || symbol} · ${quoteSeries.length} intraday bars`);
      renderQuoteChart();
      if (isNum(q.lastPrice)) { byId("spot").value = round4(q.lastPrice); scheduleAnalysis(); }
    } catch (err) {
      if (err.name === "AbortError") return;
      if (status) status.innerHTML = "<i></i> Quote unavailable";
      setText("quote-source", "Could not load provider quote.");
    }
  }

  async function showSuggestions(query) {
    const box = byId("symbol-suggestions");
    if (!box) return;
    const q = (query || "").trim();
    if (q.length < 1) { box.innerHTML = ""; return; }
    if (suggestionRequest) suggestionRequest.abort();
    suggestionRequest = new AbortController();
    try {
      const data = await fetchJSON(`/api/nse-symbols?q=${encodeURIComponent(q)}`, { signal: suggestionRequest.signal });
      const stocks = (data.stocks || []).slice(0, 8);
      box.innerHTML = stocks.length
        ? stocks.map((s) => `<button data-symbol="${escapeHtml(s.symbol)}"><b>${escapeHtml(s.symbol)}</b><span>${escapeHtml(s.name || "")}</span></button>`).join("")
        : "<p>No matching NSE symbols</p>";
    } catch (err) {
      if (err.name !== "AbortError") box.innerHTML = "";
    }
  }

  const scheduleAnalysis = () => { clearTimeout(analysisTimer); analysisTimer = setTimeout(runAnalysis, 180); };
  const scheduleSuggest = (q) => { clearTimeout(suggestionTimer); suggestionTimer = setTimeout(() => showSuggestions(q), 150); };

  // ---------------------------------------------------------------------------
  // Event wiring
  // ---------------------------------------------------------------------------
  function bindEvents() {
    ["spot", "strike", "vol", "rate", "maturity"].forEach((id) => {
      const el = byId(id);
      if (el) el.addEventListener("input", scheduleAnalysis);
    });
    ["type", "strategy"].forEach((id) => {
      const el = byId(id);
      if (el) el.addEventListener("change", () => {
        if (id === "strategy") setText("strategy-title", STRATEGY_TITLES[byId("strategy").value] || "Strategy payoff");
        runAnalysis();
      });
    });

    document.querySelectorAll("[data-scenario]").forEach((btn) => btn.addEventListener("click", () => {
      const presets = {
        conservative: { vol: 0.15, rate: 0.04, maturity: 0.5 },
        base: { vol: 0.25, rate: 0.05, maturity: 1 },
        aggressive: { vol: 0.45, rate: 0.06, maturity: 2 },
      };
      const p = presets[btn.dataset.scenario];
      if (!p) return;
      byId("vol").value = p.vol; byId("rate").value = p.rate; byId("maturity").value = p.maturity;
      runAnalysis();
    }));

    ["run-btn", "run-top", "run-hero", "refresh-btn"].forEach((id) => {
      const el = byId(id);
      if (el) el.addEventListener("click", () => { runAnalysis(); if (id === "refresh-btn" && byId("symbol").value) loadQuote(); });
    });

    const reset = byId("reset-btn");
    if (reset) reset.addEventListener("click", () => {
      const d = { spot: 100, strike: 100, vol: 0.25, rate: 0.05, maturity: 1 };
      Object.entries(d).forEach(([k, v]) => { byId(k).value = v; });
      byId("type").value = "call";
      runAnalysis();
    });

    // Symbol search + autocomplete
    const symbol = byId("symbol");
    if (symbol) {
      symbol.addEventListener("input", () => scheduleSuggest(symbol.value));
      symbol.addEventListener("keydown", (evt) => {
        if (evt.key === "Enter") { byId("symbol-suggestions").innerHTML = ""; loadQuote(); }
      });
    }
    const quoteBtn = byId("quote-btn");
    if (quoteBtn) quoteBtn.addEventListener("click", loadQuote);
    const suggestBox = byId("symbol-suggestions");
    if (suggestBox) suggestBox.addEventListener("click", (evt) => {
      const btn = evt.target.closest("button[data-symbol]");
      if (!btn) return;
      byId("symbol").value = btn.dataset.symbol;
      suggestBox.innerHTML = "";
      loadQuote();
    });
    document.addEventListener("click", (evt) => {
      if (suggestBox && !evt.target.closest(".symbol-search")) suggestBox.innerHTML = "";
    });

    // Greeks segmented control (Delta / Gamma / Vega / Theta / Price)
    document.querySelectorAll(".segmented button").forEach((btn) => btn.addEventListener("click", () => {
      document.querySelectorAll(".segmented button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      greek = btn.textContent.trim().toLowerCase();
      renderGreekChart(readInputs());
      renderSurface(result);
    }));

    // Risk shock sliders
    ["spot-shock", "iv-shock"].forEach((id) => {
      const el = byId(id);
      if (el) el.addEventListener("input", updateScenario);
    });

    // Chrome: theme toggle, mobile sidebar, copy summary, tab styling
    const theme = byId("theme-btn");
    if (theme) theme.addEventListener("click", () => {
      const light = document.body.classList.toggle("light");
      theme.innerHTML = light ? "◑ <span>Dark mode</span>" : "◐ <span>Light mode</span>";
      document.querySelectorAll("canvas").forEach((c) => repaint(c));
    });
    const menu = byId("menu-btn");
    if (menu) menu.addEventListener("click", () => byId("sidebar").classList.toggle("open"));
    document.querySelectorAll(".tabs .tab").forEach((tab) => tab.addEventListener("click", () => {
      document.querySelectorAll(".tabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
    }));
    const copy = byId("copy-btn");
    if (copy) copy.addEventListener("click", copySummary);
    document.addEventListener("keydown", (evt) => {
      if (evt.altKey && (evt.key === "p" || evt.key === "P")) { evt.preventDefault(); runAnalysis(); }
    });

  }

  function copySummary() {
    const inp = readInputs();
    const r = result || {};
    const lines = [
      "QuantSight analytics summary",
      `Spot ${inp.spot} · Strike ${inp.strike} · Vol ${percent(inp.vol)} · Rate ${percent(inp.rate)} · Expiry ${inp.maturity}y · ${inp.type}`,
      `Black–Scholes ${money(r.models?.blackScholes)} · Binomial ${money(r.models?.binomialAmerican)} · Monte Carlo ${money(r.models?.monteCarloAntithetic)}`,
      `Estimated IV ${percent(r.impliedVol?.estimatedIv)} · Realized vol ${percent(r.volatility?.realizedVol)}`,
      `Strategy ${inp.strategy}`,
    ];
    const text = lines.join("\n");
    const done = () => { const b = byId("copy-btn"); if (b) { const t = b.textContent; b.textContent = "✓ Copied"; setTimeout(() => (b.textContent = t), 1400); } };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done).catch(done);
    else done();
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------
  function init() {
    bindEvents();
    setText("strategy-title", STRATEGY_TITLES[byId("strategy").value] || "Strategy payoff");
    renderQuoteChart();
    runAnalysis();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();















})();
