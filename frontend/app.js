(() => {
  const byId = (id) => document.getElementById(id);
  const number = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  const percent = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
  let result = null;
  let greek = "delta";
  let quoteSeries = null;
  let suggestionTimer;
  let suggestionRequest;

  function drawChart(id, rows, key, colour, { zero = false, overlay } = {}) {
    const element = byId(id);
    const context = element?.getContext?.("2d");
    const values = (rows || []).map((row) => Number(row[key])).filter(Number.isFinite);
    if (!context || !values.length) return;
    const width = Math.max(element.clientWidth || 0, 260);
    const height = element.getAttribute("height") ? Number(element.getAttribute("height")) : 210;
    const scale = window.devicePixelRatio || 1;
    element.width = width * scale;
    element.height = height * scale;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#0b1728";
    context.fillRect(0, 0, width, height);
    const all = overlay ? values.concat(overlay.map((row) => Number(row[key])).filter(Number.isFinite)) : values;
    const low = Math.min(...all, zero ? 0 : Infinity);
    const high = Math.max(...all, zero ? 0 : -Infinity);
    const range = Math.max(high - low, 0.0001);
    const pad = 24;
    const y = (value) => pad + ((high - value) / range) * (height - pad * 2);
    context.strokeStyle = "#263950";
    context.lineWidth = 1;
    for (let i = 0; i < 4; i += 1) {
      const lineY = pad + i * (height - pad * 2) / 3;
      context.beginPath(); context.moveTo(pad, lineY); context.lineTo(width - pad, lineY); context.stroke();
    }
    if (zero && low < 0 && high > 0) {
      context.strokeStyle = "#7b8da7";
      context.beginPath(); context.moveTo(pad, y(0)); context.lineTo(width - pad, y(0)); context.stroke();
    }
    const line = (points, stroke) => {
      context.strokeStyle = stroke; context.lineWidth = 2.5; context.beginPath();
      points.forEach((row, index) => {
        const x = pad + index * (width - pad * 2) / Math.max(points.length - 1, 1);
        index ? context.lineTo(x, y(Number(row[key]))) : context.moveTo(x, y(Number(row[key])));
      });
      context.stroke();
    };
    line(rows, colour);
    if (overlay?.length) line(overlay, "#47d7a1");
  }

  function volatilityRows(data) {
    const iv = data.impliedVol.marketIv;
    const rv = data.volatility.realizedVol || data.impliedVol.modelIv;
    return Array.from({ length: 24 }, (_, index) => ({
      iv: iv * (0.94 + Math.sin(index / 3) * 0.04),
      rv: rv * (0.92 + Math.cos(index / 4) * 0.06),
    }));
  }

  function simulatedSurface(data) {
    const supplied = Array.isArray(data.surface) ? data.surface.filter(Boolean) : [];
    if (supplied.length) return supplied;
    const spot = Number(data.spot || byId("spot")?.value || 100);
    return [7, 30, 60].flatMap((dte) => [-0.15, -0.075, 0, 0.075, 0.15].map((move) => ({
      dte, strike: spot * (1 + move), delta: 0.5 - move * 2, gamma: 0.02, vega: 0.1, theta: -0.03,
    })));
  }

  function simulatedPayoff(data) {
    const supplied = Array.isArray(data.payoff) ? data.payoff.filter(Boolean) : [];
    if (supplied.length) return supplied;
    const strike = Number(byId("strike")?.value || 100);
    const premium = Number(data.modelPrice || 0);
    return Array.from({ length: 41 }, (_, index) => {
      const price = strike * (0.5 + index / 40);
      return { pnl: price - strike - premium };
    });
  }

  function simulatedQuote(data) {
    const spot = Number(byId("spot")?.value || data.spot || 100);
    return Array.from({ length: 48 }, (_, index) => ({
      close: spot * (1 + 0.012 * Math.sin(index / 4) + 0.004 * Math.cos(index / 2)),
    }));
  }

  function renderSimulatedCharts(data, surface, payoff) {
    // Charts are deliberately rendered first and independently. A problem in
    // an optional text panel must never prevent the simulations from drawing.
    requestAnimationFrame(() => {
      if (result !== data) return;
      try { drawChart("quote-chart", quoteSeries || simulatedQuote(data), "close", "#47d7a1"); } catch (error) { console.error("Quote chart failed", error); }
      try { drawChart("payoff-chart", payoff, "pnl", "#73a7ff", { zero: true }); } catch (error) { console.error("Payoff chart failed", error); }
      try {
        const greekRows = surface.filter((row) => Number(row.dte) === 30).map((row) => ({ value: Number(row[greek]) }));
        drawChart("greek-chart", greekRows, "value", "#b28cff", { zero: greek === "theta" });
      } catch (error) { console.error("Greek chart failed", error); }
      try {
        const rows = volatilityRows(data);
        drawChart("vol-chart", rows, "iv", "#b28cff", { overlay: rows.map(({ rv }) => ({ iv: rv })) });
      } catch (error) { console.error("Volatility chart failed", error); }
    });
  }

  function updateScenario() {
    if (!result) return;
    const spotMove = Number(byId("spot-shock").value) / 100;
    const ivMove = Number(byId("iv-shock").value) / 100;
    const spot = Number(byId("spot").value);
    const shockedSpot = spot * (1 + spotMove);
    const shockedVol = Math.max(Number(byId("vol").value) + ivMove, 0.0001);
    const pnl = scenarioValue(result.strategyLegs, shockedSpot, Number(byId("strike").value),
      Number(byId("rate").value), Number(byId("maturity").value), shockedVol) - Number(result.positionCost);
    byId("spot-shock-value").textContent = `${(spotMove * 100).toFixed(0)}%`;
    byId("iv-shock-value").textContent = `${(ivMove * 100).toFixed(0)}%`;
    const output = byId("scenario-pnl");
    output.textContent = `${pnl >= 0 ? "+" : ""}₹ ${number(pnl)}`;
    output.className = pnl >= 0 ? "gain" : "loss";
    byId("vega-limit").textContent = number(result.greeks.vega);
  }

  function normalCdf(value) {
    return 0.5 * (1 + erf(value / Math.sqrt(2)));
  }

  function erf(value) {
    const sign = value < 0 ? -1 : 1;
    const x = Math.abs(value);
    const t = 1 / (1 + 0.3275911 * x);
    const polynomial = (((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t);
    return sign * (1 - polynomial * Math.exp(-x * x));
  }

  function blackScholesValue(spot, strike, rate, maturity, vol, type) {
    const sqrtMaturity = Math.sqrt(maturity);
    const d1 = (Math.log(spot / strike) + (rate + 0.5 * vol ** 2) * maturity) / (vol * sqrtMaturity);
    const d2 = d1 - vol * sqrtMaturity;
    const discount = Math.exp(-rate * maturity);
    if (type === "call") return spot * normalCdf(d1) - strike * discount * normalCdf(d2);
    return strike * discount * normalCdf(-d2) - spot * normalCdf(-d1);
  }

  function scenarioValue(legs, spot, referenceStrike, rate, maturity, vol) {
    if (!Array.isArray(legs)) return Number.NaN;
    return legs.reduce((total, leg) => {
      const quantity = Number(leg.quantity);
      if (!Number.isFinite(quantity)) return total;
      if (leg.kind === "stock") return total + quantity * spot;
      const strike = Number(leg.strike || referenceStrike);
      return total + quantity * blackScholesValue(spot, strike, rate, maturity, vol, leg.type);
    }, 0);
  }

  function render(data) {
    result = data;
    const surface = simulatedSurface(data);
    const payoff = simulatedPayoff(data);
    renderSimulatedCharts(data, surface, payoff);
    byId("m-price").textContent = `₹ ${number(data.modelPrice)}`;
    byId("m-iv").textContent = percent(data.impliedVol.marketIv);
    byId("m-rv").textContent = percent(data.volatility.realizedVol);
    byId("m-latency").textContent = `${number(data.requestMs)} ms`;
    byId("m-signal").textContent = `${data.impliedVol.signal} IV signal`;
    byId("request-time").textContent = `${number(data.requestMs)} ms`;
    byId("vol-regime").textContent = data.volatility.regime;
    byId("vol-regime").className = `pill ${data.volatility.regime}`;
    byId("vol-info").innerHTML = [["Garman–Klass", percent(data.volatility.gkVol)], ["30d forecast", percent(data.volatility.forward30d)], ["IV / RV", `${number(data.volatility.ivVsRealized)}×`]].map(([label, value]) => `<span>${label} <b>${value}</b></span>`).join("");
    const models = [["blue", "Black–Scholes", "European benchmark", data.models.blackScholes], ["purple", "Binomial tree", "American exercise", data.models.binomialAmerican], ["green", "Monte Carlo", "Antithetic variates", data.models.monteCarloAntithetic]];
    byId("models").innerHTML = models.map(([colour, name, description, price], index) => `<div class="model-row ${index === 0 ? "featured" : ""}"><span class="model-dot ${colour}"></span><div><b>${name}</b><small>${description}</small></div><strong>₹ ${number(price)}</strong></div>`).join("");
    byId("model-note").textContent = `Binomial premium: ₹ ${number(data.models.binomialAmerican - data.models.blackScholes)} · Market-model gap: ₹ ${number(data.priceDifference)}`;
    byId("greek-cards").innerHTML = Object.entries(data.greeks).map(([name, value]) => `<article><small>${name}</small><strong>${number(value)}</strong><span>${name === "theta" ? "annual time decay" : name === "vega" ? "volatility sensitivity" : "current exposure"}</span></article>`).join("");
    const title = greek[0].toUpperCase() + greek.slice(1);
    byId("surface-title").textContent = `${title} surface`;
    byId("surface-metric").textContent = greek === "delta" ? "Δ" : title;
    byId("greek-chart-caption").textContent = `${title} response across strike levels`;
    byId("surface").querySelector("tbody").innerHTML = surface.map((row) => `<tr><td>${row.dte}d</td><td>${number(row.strike)}</td><td style="--v:${Math.min(1, Math.abs(row[greek]))}">${number(row[greek])}</td><td>${number(row.gamma)}</td><td>${number(row.vega)}</td></tr>`).join("");
    const strategy = byId("strategy");
    byId("strategy-title").textContent = `${strategy?.selectedOptions?.[0]?.text || strategy?.options?.[strategy?.selectedIndex]?.text || "Strategy"} payoff`;
    const metrics = data.strategyMetrics || {};
    const money = (value) => typeof value === "string" ? value : `₹ ${number(value)}`;
    const breakEvens = Array.isArray(metrics.breakEvens) && metrics.breakEvens.length
      ? metrics.breakEvens.map((value) => `₹ ${number(value)}`).join(" / ")
      : "—";
    byId("payoff-metrics").innerHTML = `<span>Max loss <b>${money(metrics.maxLoss)}</b></span><span>Break-even <b>${breakEvens}</b></span><span>Max gain <b>${money(metrics.maxGain)}</b></span>`;
    updateScenario();
  }

  function requestData() {
    return { spot: Number(byId("spot").value), strike: Number(byId("strike").value), vol: Number(byId("vol").value), rate: Number(byId("rate").value), maturity: Number(byId("maturity").value), option_type: byId("type").value, strategy: byId("strategy").value, series: quoteSeries || [] };
  }

  async function runAnalysis(event) {
    const controls = [byId("run-btn"), byId("run-top"), byId("run-hero")];
    controls.forEach((button) => { button.disabled = true; });
    byId("request-time").textContent = "Calculating…";
    try {
      const response = await fetch("/api/analytics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestData()) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Analytics request failed");
      render(payload);
    } catch (error) {
      byId("request-time").textContent = `Analysis failed: ${error.message}`;
      byId("model-note").textContent = "Unable to load analytics. Check that the dashboard is opened through the FastAPI server.";
    } finally {
      controls.forEach((button) => { button.disabled = false; });
    }
  }

  async function loadQuote() {
    const symbol = byId("symbol").value.trim();
    if (!symbol) return;
    const button = byId("quote-btn");
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      const response = await fetch(`/api/stock?market=equity&symbol=${encodeURIComponent(symbol)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Quote unavailable");
      const quote = payload.quote;
      quoteSeries = Array.isArray(quote.series) && quote.series.length ? quote.series : null;
      byId("spot").value = quote.lastPrice;
      byId("quote-symbol").textContent = quote.symbol;
      byId("quote-last").textContent = `₹ ${number(quote.lastPrice)}`;
      byId("quote-change").textContent = quote.change == null ? "Quote loaded" : `${quote.change >= 0 ? "▲" : "▼"} ${number(quote.change)}`;
      byId("quote-stats").innerHTML = [["Open", quote.openPrice], ["High", quote.highPrice], ["Low", quote.lowPrice]].map(([label, value]) => `<span>${label} <b>₹ ${number(value)}</b></span>`).join("");
      requestAnimationFrame(() => drawChart("quote-chart", quoteSeries || simulatedQuote({}), "close", quote.change >= 0 ? "#47d7a1" : "#fb7185"));
      runAnalysis();
    } catch (error) {
      byId("quote-change").textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "Fetch quote";
    }
  }

  async function showSuggestions() {
    const input = byId("symbol");
    const menu = byId("symbol-suggestions");
    const query = input.value.trim();
    if (!query) {
      menu.innerHTML = "";
      return;
    }
    suggestionRequest?.abort();
    suggestionRequest = new AbortController();
    try {
      const response = await fetch(`/api/nse-symbols?q=${encodeURIComponent(query)}`, { signal: suggestionRequest.signal });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Symbol search unavailable");
      const stocks = payload.stocks || [];
      menu.innerHTML = stocks.slice(0, 8).map((stock) => `<button type="button" data-symbol="${escapeHtml(stock.symbol)}"><b>${escapeHtml(stock.symbol)}</b><span>${escapeHtml(stock.name || "NSE Equity")}</span></button>`).join("") || "<p>No NSE equity found</p>";
      menu.querySelectorAll("button[data-symbol]").forEach((button) => button.addEventListener("click", () => {
        input.value = button.dataset.symbol;
        menu.innerHTML = "";
        loadQuote();
      }));
    } catch (error) {
      if (error.name !== "AbortError") menu.innerHTML = "<p>Symbol search is temporarily unavailable.</p>";
    }
  }

  ["run-btn", "run-top", "run-hero", "refresh-btn"].forEach((id) => { byId(id).onclick = runAnalysis; });
  byId("quote-btn").onclick = loadQuote;
  byId("symbol").addEventListener("input", () => {
    clearTimeout(suggestionTimer);
    suggestionTimer = setTimeout(showSuggestions, 180);
  });
  byId("symbol").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); loadQuote(); }
  });
  byId("strategy").onchange = runAnalysis;
  byId("spot-shock").oninput = updateScenario;
  byId("iv-shock").oninput = updateScenario;
  document.querySelectorAll(".segmented button").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".segmented button").forEach((item) => item.classList.toggle("active", item === button));
    greek = button.textContent.trim().toLowerCase();
    if (result) render(result);
  }));
  document.querySelectorAll("[data-scenario]").forEach((button) => button.addEventListener("click", () => {
    const scenario = { conservative: [0.16, 0.04], base: [0.25, 0.05], aggressive: [0.42, 0.06] }[button.dataset.scenario];
    if (!scenario) return;
    byId("vol").value = scenario[0];
    byId("rate").value = scenario[1];
    runAnalysis();
  }));
  byId("reset-btn").onclick = () => {
    [["spot", 100], ["strike", 100], ["vol", 0.25], ["rate", 0.05], ["maturity", 1]].forEach(([id, value]) => { byId(id).value = value; });
    quoteSeries = null;
    runAnalysis();
  };
  byId("theme-btn").onclick = () => {
    document.body.classList.toggle("light");
    byId("theme-btn").querySelector("span").textContent = document.body.classList.contains("light") ? "Dark mode" : "Light mode";
  };
  byId("menu-btn").onclick = () => byId("sidebar").classList.toggle("open");
  byId("copy-btn").onclick = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(`Option price: INR ${number(result.modelPrice)} | IV: ${percent(result.impliedVol.marketIv)} | Delta: ${number(result.greeks.delta)}`);
      byId("copy-btn").textContent = "Copied";
    } catch (_) {
      byId("copy-btn").textContent = "Copy unavailable";
    }
    window.setTimeout(() => { byId("copy-btn").textContent = "Copy summary"; }, 1300);
  };
  document.addEventListener("keydown", (event) => {
    if (event.altKey && event.key.toLowerCase() === "p") { event.preventDefault(); runAnalysis(); }
  });
  window.addEventListener("resize", () => result && render(result));
  runAnalysis();
})();
